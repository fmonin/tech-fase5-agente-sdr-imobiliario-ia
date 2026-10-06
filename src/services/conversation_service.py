"""Serviço de aplicação: orquestra uma "rodada" de conversa.

Esta é a camada de "casos de uso" (use cases), que fica entre a interface
(Streamlit/Telegram) e o grafo multiagente. Responsabilidade única: dado um
lead e uma mensagem, (1) recuperar/atualizar o estado persistido do lead,
(2) rodar o grafo LangGraph, (3) aplicar o resultado de volta nas
entidades de domínio e (4) registrar eventos de observabilidade/CRM.

Nem a interface Streamlit, nem o bot do Telegram, sabem como o LangGraph
funciona por dentro — eles só chamam `ConversationService.processar_mensagem`.
Isso é Dependency Inversion + Single Responsibility em ação: a interface
depende de um serviço de alto nível, não dos detalhes do grafo.
"""
from __future__ import annotations

from datetime import datetime

from src.agents.state import EstadoConversa
from src.domain.entities import Agendamento, IntencaoLead, Lead, RemetenteMensagem, TemperaturaLead
from src.domain.interfaces import ICRM, ILeadRepository, IObservador


class ConversationService:
    def __init__(
        self,
        grafo_compilado,
        lead_repository: ILeadRepository,
        crm: ICRM,
        observador: IObservador,
        resumidor=None,  # ResumidorAgent (opcional): resumo sob demanda no painel
    ) -> None:
        self._resumidor = resumidor
        self._grafo = grafo_compilado
        self._leads = lead_repository
        self._crm = crm
        self._observador = observador

    def obter_ou_criar_lead(self, lead_id: str | None, canal: str = "web") -> Lead:
        if lead_id:
            lead_existente = self._leads.buscar_por_id(lead_id)
            if lead_existente:
                return lead_existente
        lead = Lead(canal=canal)
        self._leads.salvar(lead)
        return lead

    def buscar_lead(self, lead_id: str) -> Lead | None:
        return self._leads.buscar_por_id(lead_id)

    def gerar_resumo(self, lead_id: str) -> str | None:
        """Gera (ou atualiza) o resumo do lead para o corretor, sob demanda —
        usado pelos botões do Painel e da Área do Corretor."""
        lead = self._leads.buscar_por_id(lead_id)
        if lead is None or self._resumidor is None:
            return None
        estado = self._montar_estado_inicial(lead, "")
        if lead.agendamentos:
            estado["agendamento_sugerido"] = lead.agendamentos[-1].quando_sugerido
        lead.resumo_corretor = self._resumidor(estado)["resumo_corretor"]
        self._leads.salvar(lead)
        return lead.resumo_corretor

    def processar_mensagem(self, lead: Lead, mensagem_usuario: str) -> Lead:
        """Processa uma mensagem do lead e retorna o Lead atualizado
        (já com a resposta do agente registrada no histórico).

        Observação sobre cadastro/identificação: o lead recebido aqui pode
        ser um lead TEMPORÁRIO (criado só para a sessão atual, antes do
        cliente se identificar). Se o `IdentificacaoAgent` reconhecer, pelo
        CPF, que se trata de um cliente recorrente, a conversa "troca de
        lead" no meio do caminho — veja `_resolver_lead_alvo` — e o Lead
        já existente (com todo o histórico anterior) é retornado no lugar
        do temporário."""
        self._observador.registrar_evento(
            "mensagem_recebida", {"lead_id": lead.id, "canal": lead.canal, "texto": mensagem_usuario}
        )
        lead.registrar_mensagem(RemetenteMensagem.LEAD, mensagem_usuario)

        estado_inicial = self._montar_estado_inicial(lead, mensagem_usuario)
        estado_final: EstadoConversa = self._grafo.invoke(estado_inicial)

        lead_alvo = self._resolver_lead_alvo(lead, estado_final, mensagem_usuario)

        if lead_alvo is lead:
            self._aplicar_estado_no_lead(lead_alvo, estado_final)
        else:
            # Cliente recorrente recuperado pelo CPF: o `estado_final` deste
            # turno foi montado a partir do lead TEMPORÁRIO (perfil vazio).
            # Aplicá-lo no lead recuperado apagaria o perfil salvo (intenção,
            # região, quartos...) — era exatamente o bug de o agente
            # "esquecer" a conversa anterior e perguntar a região de novo.
            # Por isso, aqui só aplicamos os dados de identificação.
            self._aplicar_identificacao_no_lead(lead_alvo, estado_final)

        if estado_final.get("resumo_corretor"):
            lead_alvo.resumo_corretor = estado_final["resumo_corretor"]
        anterior = estado_final.get("busca_anterior")
        if anterior and anterior not in lead_alvo.buscas_anteriores:
            lead_alvo.buscas_anteriores.append(anterior)

        resposta = estado_final.get("resposta_agente") or (
            "Certo! Já registrei suas informações, em breve um corretor entra em contato."
        )
        lead_alvo.registrar_mensagem(RemetenteMensagem.AGENTE, resposta)

        self._leads.salvar(lead_alvo)
        self._crm.registrar_lead(lead_alvo)

        alterado = estado_final.get("agendamento_alterado_pelo_cliente")
        if alterado is not None:
            self._observador.registrar_evento(
                "agendamento_cancelado_pelo_cliente" if alterado.status == "cancelado"
                else "agendamento_remarcado_pelo_cliente",
                {"lead_id": lead_alvo.id, "agendamento_id": alterado.id, "corretor_id": alterado.corretor_id,
                 "quando": alterado.quando_sugerido},
            )

        self._observador.registrar_evento(
            "turno_processado",
            {
                "lead_id": lead_alvo.id,
                "intencao": estado_final.get("intencao"),
                "temperatura": estado_final.get("temperatura"),
                "agendamento_sugerido": estado_final.get("agendamento_sugerido"),
                "gerou_resumo": bool(estado_final.get("resumo_corretor")),
            },
        )
        return lead_alvo

    def _resolver_lead_alvo(
        self, lead: Lead, estado_final: EstadoConversa, mensagem_usuario: str
    ) -> Lead:
        """Se o `IdentificacaoAgent` recuperou um cliente recorrente (pelo
        CPF), a conversa passa a continuar no lead JÁ EXISTENTE dele — com
        todo o histórico anterior — em vez do lead temporário da sessão
        atual. Transplantamos a mensagem do turno atual para o lead
        recuperado, para não perder o que o cliente acabou de dizer."""
        lead_id_recuperado = estado_final.get("lead_id_recuperado")
        if not lead_id_recuperado or lead_id_recuperado == lead.id:
            return lead

        lead_recuperado = self._leads.buscar_por_id(lead_id_recuperado)
        if not lead_recuperado:
            return lead

        lead_recuperado.registrar_mensagem(RemetenteMensagem.LEAD, mensagem_usuario)
        return lead_recuperado

    @staticmethod
    def _montar_estado_inicial(lead: Lead, mensagem_usuario: str) -> EstadoConversa:
        perfil = lead.perfil
        return EstadoConversa(
            lead_id=lead.id,
            mensagem_usuario=mensagem_usuario,
            historico_mensagens=[
                {"role": "user" if m.remetente == RemetenteMensagem.LEAD else "assistant", "content": m.conteudo}
                for m in lead.historico
            ],
            agendamentos_anteriores=[
                f"{a.tipo_texto} {a.quando_sugerido}"
                + (f" com {a.corretor_nome}" if a.corretor_nome else "")
                + f" ({a.status})"
                for a in lead.agendamentos
            ],
            cliente_identificado=lead.cliente_identificado,
            cliente_cpf=lead.cpf,
            cliente_nome=lead.nome,
            intencao=perfil.intencao.value,
            faixa_preco_min=perfil.faixa_preco_min,
            faixa_preco_max=perfil.faixa_preco_max,
            quartos_desejados=perfil.quartos_desejados,
            regiao_interesse=perfil.regiao_interesse,
            urgencia=perfil.urgencia,
            ticket_investimento=perfil.ticket_investimento,
            expectativa_retorno=perfil.expectativa_retorno,
            temperatura=perfil.temperatura.value,
            imovel_interesse_id=perfil.imovel_interesse_id,
            buscas_anteriores=list(lead.buscas_anteriores),
            feedback_visitas=list(lead.feedback_visitas),
            captacao=dict(lead.captacao),
        )

    @staticmethod
    def _aplicar_identificacao_no_lead(lead: Lead, estado_final: EstadoConversa) -> None:
        if estado_final.get("cliente_cpf"):
            lead.cpf = estado_final["cliente_cpf"]
        if estado_final.get("cliente_nome"):
            lead.nome = estado_final["cliente_nome"]

    @classmethod
    def _aplicar_estado_no_lead(cls, lead: Lead, estado_final: EstadoConversa) -> None:
        cls._aplicar_identificacao_no_lead(lead, estado_final)

        perfil = lead.perfil
        # Só sobrescreve um campo quando veio um valor de verdade: um None no
        # estado nunca apaga algo que o lead já informou antes.
        intencao = estado_final.get("intencao")
        if intencao and (
            estado_final.get("recomecar_busca")
            or not (intencao == "indefinida" and perfil.intencao != IntencaoLead.INDEFINIDA)
        ):
            perfil.intencao = IntencaoLead(intencao)
        for campo in (
            "faixa_preco_min",
            "faixa_preco_max",
            "quartos_desejados",
            "regiao_interesse",
            "urgencia",
            "ticket_investimento",
            "expectativa_retorno",
        ):
            valor = estado_final.get(campo)
            # Exceção: numa NOVA BUSCA (o lead trocou de intenção), os campos
            # da busca anterior são limpos de propósito.
            if valor is not None or estado_final.get("nova_busca"):
                setattr(perfil, campo, valor)
        if estado_final.get("imovel_interesse_id"):
            perfil.imovel_interesse_id = estado_final["imovel_interesse_id"]
        elif estado_final.get("nova_busca"):
            perfil.imovel_interesse_id = None
        if estado_final.get("temperatura"):
            perfil.temperatura = TemperaturaLead(estado_final["temperatura"])

        # Encerramento pelo cliente: sem follow-up até ele voltar a falar.
        lead.atendimento_encerrado = bool(estado_final.get("atendimento_encerrado"))
        lead.nao_contatar = bool(estado_final.get("nao_contatar"))  # voltou a falar = pode contatar de novo
        if estado_final.get("nota_atendimento"):
            lead.notas_atendimento.append(int(estado_final["nota_atendimento"]))
        if "captacao" in estado_final:
            lead.captacao = estado_final.get("captacao") or {}
        if estado_final.get("captacao_concluida"):
            lead.captacoes.append(estado_final["captacao_concluida"])
        novo = estado_final.get("agendamento_novo")
        if novo is not None and not any(a.id == novo.id for a in lead.agendamentos):
            lead.agendamentos.append(novo)

        novo_feedback = estado_final.get("feedback_visita_novo")
        if novo_feedback and not any(f.get("imovel_id") == novo_feedback.get("imovel_id") for f in lead.feedback_visitas):
            lead.feedback_visitas.append(novo_feedback)

        alterado = estado_final.get("agendamento_alterado_pelo_cliente")
        if alterado is not None:
            # O cliente cancelou/remarcou pelo chat: reflete no histórico do lead
            # (pelo id; agendamentos antigos casam pelo corretor + horário anterior).
            antes = estado_final.get("agendamento_alterado_antes")
            for existente in lead.agendamentos:
                if existente.id == alterado.id or (
                    existente.status != "cancelado"
                    and existente.corretor_id == alterado.corretor_id
                    and existente.quando_sugerido == antes
                ):
                    for campo in ("status", "confirmado", "quando_sugerido", "data_hora", "cancelado_por",
                                  "cancelado_em", "horario_anterior", "motivo_alteracao", "cliente_notificado",
                                  "corretor_notificado"):
                        setattr(existente, campo, getattr(alterado, campo))

        horario_sugerido = estado_final.get("agendamento_sugerido")
        status = estado_final.get("agendamento_status") or "sugerido"
        cancelado = estado_final.get("agendamento_cancelado")
        cancelado_id = estado_final.get("agendamento_cancelado_id")
        agendamento_id = estado_final.get("agendamento_id")
        # O histórico do lead usa o MESMO id da base de agenda. (Antes a
        # ligação era pelo texto "amanhã às 10h", e um horário novo com o mesmo
        # texto de um cancelado "ressuscitava" o antigo em vez de ser criado.)
        for existente in lead.agendamentos:
            if existente.status == "sugerido" and (
                (cancelado_id and existente.id == cancelado_id)
                or (not cancelado_id and cancelado and existente.quando_sugerido == cancelado)
            ):
                existente.status = "cancelado"
            mesmo = existente.id == agendamento_id if agendamento_id else existente.quando_sugerido == horario_sugerido
            if horario_sugerido and mesmo:
                existente.status = status
                existente.confirmado = status == "confirmado"
        if status == "confirmado" and agendamento_id and not any(a.id == agendamento_id for a in lead.agendamentos):
            # agendamento antigo (criado antes desta correção, com outro id): casa pelo texto
            for existente in lead.agendamentos:
                if existente.quando_sugerido == horario_sugerido and existente.status == "sugerido":
                    existente.status, existente.confirmado = "confirmado", True
                    agendamento_id = existente.id
        ja_registrado = any(
            (a.id == agendamento_id) if agendamento_id else (a.quando_sugerido == horario_sugerido)
            for a in lead.agendamentos
        )
        if horario_sugerido and not ja_registrado:
            data_hora_str = estado_final.get("agendamento_data_hora")
            lead.agendamentos.append(
                Agendamento(
                    **({"id": agendamento_id} if agendamento_id else {}),
                    lead_id=lead.id,
                    quando_sugerido=horario_sugerido,
                    tipo=estado_final.get("tipo_agendamento", "reuniao"),
                    status="sugerido",
                    data_hora=datetime.fromisoformat(data_hora_str) if data_hora_str else None,
                    cliente_cpf=lead.cpf,
                    cliente_nome=lead.nome,
                    corretor_id=estado_final.get("corretor_id"),
                    corretor_nome=estado_final.get("corretor_nome"),
                    imovel_id=estado_final.get("imovel_interesse_id"),
                    imovel_titulo=estado_final.get("agendamento_imovel_titulo"),
                )
            )
