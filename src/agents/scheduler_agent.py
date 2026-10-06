"""Agente Agendador.

Responsabilidade única: quando o lead demonstra intenção de marcar uma
reunião/visita (ou já está "quente" e com dados completos), sugerir um
horário, ESCOLHER AUTOMATICAMENTE o corretor certo (com base na zona do
imóvel/interesse do lead) e registrar o agendamento numa base consultável
— sem se preocupar em qualificar o lead ou buscar imóveis, isso já foi
feito pelos agentes anteriores no grafo.

Como funciona a atribuição automática de corretor:
    1. Descobre a zona relevante: a do primeiro imóvel sugerido (mais
       preciso) ou, na falta dele, a região de interesse informada pelo
       lead.
    2. Busca, no `ICorretorRepository`, os corretores cuja área de atuação
       cobre essa zona.
    3. Se mais de um corretor cobrir a mesma zona, o desempate é por
       MENOR carga (`IAgendaRepository.contar_agendamentos_ativos`) — o
       corretor com menos agendamentos ativos assume o próximo lead. Isso
       evita sobrecarregar sempre o mesmo corretor.
    4. Se nenhum corretor cobrir a zona (ex.: lead sem região definida),
       cai num corretor "coringa": o de menor carga entre TODOS — um
       fallback simples para não deixar o agendamento sem responsável.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.agents.contexto_conversa import historico_anterior
from src.agents.foco_imovel import resolver_imovel_citado
from src.agents.intencao_agendamento import (
    PERGUNTA_CONFIRMACAO_HORARIO,
    PERGUNTA_NOVO_HORARIO,
    eh_aceite,
    eh_pedido_de_mudanca,
    extrair_horario,
)
from src.agents.state import EstadoConversa

# "Área" usada no cadastro de corretores para o especialista em investimentos.
AREA_INVESTIMENTOS = "Investimentos"
from src.domain.entities import Agendamento, Corretor, Imovel
from src.domain.interfaces import (
    ICRM,
    IAgendaRepository,
    ICorretorRepository,
    ILLMProvider,
    IPropertyRepository,
)

# Cada opção já combina o texto amigável ("amanhã às 10h") com como
# calcular a data/hora real a partir de agora — é isso que permite ao
# corretor consultar "o que eu tenho essa semana" com uma data de verdade.
_HORARIOS_SUGERIDOS = [
    {"texto": "amanhã às 10h", "dias": 1, "hora": 10},
    {"texto": "amanhã às 15h", "dias": 1, "hora": 15},
    {"texto": "depois de amanhã às 11h", "dias": 2, "hora": 11},
]


class AgendadorAgent:
    def __init__(
        self,
        llm_provider: ILLMProvider,
        crm: ICRM,
        corretor_repository: ICorretorRepository,
        agenda_repository: IAgendaRepository,
        repositorio_imoveis: IPropertyRepository | None = None,
    ) -> None:
        self._imoveis_repo = repositorio_imoveis
        self._llm = llm_provider
        self._crm = crm
        self._corretores = corretor_repository
        self._agenda = agenda_repository

    def __call__(self, estado: EstadoConversa) -> dict:
        """Três situações (decididas em código):

        1. Há um horário PROPOSTO no turno anterior e o lead aceitou
           ("sim", "pode ser") -> CONFIRMA o agendamento existente.
        2. O lead informou um dia/horário ("sábado de manhã") -> usa esse
           horário (cancelando a proposta anterior, se houver).
        3. Caso contrário -> propõe um horário padrão.
        Antes, todo "sim" gerava um NOVO agendamento com a mesma pergunta,
        e o lead nunca conseguia confirmar (loop).
        """
        mensagem = estado.get("mensagem_usuario", "")
        pendente = self._agendamento_pendente(estado)
        horario_pedido = extrair_horario(mensagem)

        if pendente and horario_pedido is None:
            if eh_aceite(mensagem):
                return self._confirmar(pendente)
            # "Quero mudar o horário" (sem dizer qual) ou resposta que não
            # deu para entender: pergunta o novo horário SEM mexer na agenda.
            if eh_pedido_de_mudanca(mensagem):
                texto = f"Claro, sem problema! {PERGUNTA_NOVO_HORARIO} Pode me dizer, por exemplo, \"dia 8 às 13h\" ou \"sexta de manhã\"."
            else:
                texto = (
                    f"Só para confirmar: {pendente.quando_formatado()} funciona para você? "
                    f"Se preferir outro, me diga o dia e o horário (ex.: \"dia 8 às 13h\"). {PERGUNTA_NOVO_HORARIO}"
                )
            return {"resposta_agente": texto}

        if horario_pedido is None and pendente is None:
            # "Quero agendar" logo depois de ter dito "dia 8 às 13h": usa o
            # horário que o lead já pediu nas mensagens anteriores.
            horario_pedido = self._horario_pedido_antes(estado)

        if horario_pedido is not None:
            opcao_horario = {"texto": horario_pedido["texto"]}
            data_hora = horario_pedido["data_hora"]
        else:
            opcao_horario = self._escolher_horario(estado)
            data_hora = self._calcular_data_hora(opcao_horario)
        tipo = "visita" if estado.get("intencao") in ("compra", "aluguel") else "reuniao"

        if pendente is None:
            ja_marcado = self._ja_marcado_no_horario(estado, data_hora)
            if ja_marcado is not None:
                # Ex.: o cliente acabou de remarcar pelo chat e respondeu "sim" —
                # o horário já está na agenda: não cria um agendamento duplicado.
                com_quem = f" com o(a) corretor(a) {ja_marcado.corretor_nome}" if ja_marcado.corretor_nome else ""
                return {"resposta_agente": (
                    f"Sua {ja_marcado.tipo_texto}{com_quem} já está marcada para {ja_marcado.quando_formatado()} ✅ "
                    "Posso te ajudar com mais alguma coisa?")}

        primeiro_imovel = self._imovel_do_agendamento(estado)
        zona = primeiro_imovel.zona if primeiro_imovel else estado.get("regiao_interesse")
        especialista_investimento = False
        corretor = None
        if estado.get("intencao") == "investimento":
            # Exemplo 2 do desafio: investidor é direcionado ao ESPECIALISTA.
            especialistas = self._corretores.buscar_por_zona(AREA_INVESTIMENTOS)
            if especialistas:
                corretor = min(especialistas, key=lambda c: (self._agenda.contar_agendamentos_ativos(c.id), c.nome))
                especialista_investimento = True
        if corretor is None and pendente and pendente.corretor_id:
            # Trocando o horário de uma proposta: continua com o MESMO corretor
            # (antes o desempate por carga podia trocar Camila por Rafael).
            corretor = next((c for c in self._corretores.listar_todos() if c.id == pendente.corretor_id), None)
        corretor = corretor or self._escolher_corretor(zona)

        agendamento = Agendamento(
            lead_id=estado["lead_id"],
            quando_sugerido=opcao_horario["texto"],
            tipo=tipo,
            status="sugerido",
            data_hora=data_hora,
            cliente_cpf=estado.get("cliente_cpf"),
            cliente_nome=estado.get("cliente_nome"),
            corretor_id=corretor.id if corretor else None,
            corretor_nome=corretor.nome if corretor else None,
            imovel_id=primeiro_imovel.id if primeiro_imovel else None,
            imovel_titulo=primeiro_imovel.titulo if primeiro_imovel else None,
        )

        self._crm.registrar_agendamento(agendamento)
        self._agenda.registrar(agendamento)

        cancelado = None
        if pendente:  # o lead pediu outro horário: a proposta anterior sai da agenda
            pendente.status = "cancelado"
            self._agenda.registrar(pendente)
            cancelado = pendente.quando_sugerido
            cancelado_id = pendente.id
        else:
            cancelado_id = None

        resposta = self._redigir_resposta(tipo, opcao_horario["texto"], corretor, especialista_investimento)

        return {
            "agendamento_sugerido": opcao_horario["texto"],
            "agendamento_data_hora": data_hora.isoformat(),
            "agendamento_status": "sugerido",
            "agendamento_cancelado": cancelado,
            "agendamento_id": agendamento.id,
            "agendamento_cancelado_id": cancelado_id,
            "imovel_interesse_id": primeiro_imovel.id if primeiro_imovel else estado.get("imovel_interesse_id"),
            "agendamento_imovel_titulo": primeiro_imovel.titulo if primeiro_imovel else None,
            "tipo_agendamento": tipo,
            "corretor_id": corretor.id if corretor else None,
            "corretor_nome": corretor.nome if corretor else None,
            "resposta_agente": resposta,
        }

    def _ja_marcado_no_horario(self, estado: EstadoConversa, data_hora) -> Agendamento | None:
        """Agendamento ativo do mesmo cliente exatamente nesse dia e hora."""
        cpf = estado.get("cliente_cpf")
        if not cpf or data_hora is None:
            return None
        return next(
            (a for a in self._agenda.listar_por_cliente(cpf)
             if a.status != "cancelado" and a.data_hora == data_hora),
            None,
        )

    def _agendamento_pendente(self, estado: EstadoConversa) -> Agendamento | None:
        """Agendamento "sugerido" do lead, se o agente acabou de propor um
        horário (última fala do agente contém a pergunta de confirmação)."""
        ultima_fala = next(
            (m["content"] for m in reversed(historico_anterior(estado)) if m["role"] == "assistant"),
            "",
        )
        cpf = estado.get("cliente_cpf")
        if not cpf or (
            PERGUNTA_CONFIRMACAO_HORARIO not in ultima_fala and PERGUNTA_NOVO_HORARIO not in ultima_fala
        ):
            return None
        sugeridos = [
            a
            for a in self._agenda.listar_por_cliente(cpf)
            if a.lead_id == estado.get("lead_id") and a.status == "sugerido"
        ]
        return max(sugeridos, key=lambda a: a.criado_em) if sugeridos else None

    @staticmethod
    def _horario_pedido_antes(estado: EstadoConversa) -> dict | None:
        """Dia/horário que o lead pediu nas últimas mensagens (ainda não usado)."""
        historico = historico_anterior(estado)
        for m in reversed(historico[-6:]):
            if m["role"] == "assistant" and PERGUNTA_CONFIRMACAO_HORARIO in m["content"]:
                return None  # já houve uma proposta depois disso
            if m["role"] == "user":
                horario = extrair_horario(m["content"])
                if horario:
                    return horario
        return None

    def _confirmar(self, agendamento: Agendamento) -> dict:
        agendamento.status = "confirmado"
        agendamento.confirmado = True
        self._agenda.registrar(agendamento)
        self._crm.registrar_agendamento(agendamento)

        tipo_texto = agendamento.tipo_texto
        com_quem = f" com o(a) corretor(a) {agendamento.corretor_nome}" if agendamento.corretor_nome else ""
        resposta = (
            f"Combinado! Sua {tipo_texto}{com_quem} está confirmada para "
            f"{agendamento.quando_sugerido}. Você vai receber o contato do corretor "
            "para alinhar os detalhes. Qualquer dúvida até lá, é só me chamar!"
        )
        return {
            "agendamento_sugerido": agendamento.quando_sugerido,
            "agendamento_data_hora": agendamento.data_hora.isoformat() if agendamento.data_hora else None,
            "agendamento_status": "confirmado",
            "agendamento_id": agendamento.id,
            "tipo_agendamento": agendamento.tipo,
            "corretor_id": agendamento.corretor_id,
            "corretor_nome": agendamento.corretor_nome,
            "resposta_agente": resposta,
        }

    def _imovel_do_agendamento(self, estado: EstadoConversa) -> Imovel | None:
        """Qual imóvel será visitado: o sugerido NESTE turno; senão o citado na
        mensagem ("quero visitar o do Tatuapé"); senão o imóvel em foco da
        conversa (último detalhado/sugerido, salvo no perfil do lead). Assim
        o corretor escolhido é o da zona do imóvel certo."""
        sugeridos = estado.get("imoveis_sugeridos") or []
        if sugeridos:
            return sugeridos[0]
        if self._imoveis_repo is None:
            return None
        todos = self._imoveis_repo.listar_todos()
        citado = resolver_imovel_citado(estado.get("mensagem_usuario", ""), todos)
        if citado:
            return citado
        foco = estado.get("imovel_interesse_id")
        return next((im for im in todos if im.id == foco), None) if foco else None

    def _zona_relevante(self, estado: EstadoConversa) -> str | None:
        imoveis_sugeridos = estado.get("imoveis_sugeridos") or []
        if imoveis_sugeridos:
            return imoveis_sugeridos[0].zona
        return estado.get("regiao_interesse")

    def _escolher_corretor(self, zona: str | None) -> Corretor | None:
        candidatos = self._corretores.buscar_por_zona(zona) if zona else []
        veio_do_fallback = False

        if not candidatos:
            candidatos = [c for c in self._corretores.listar_todos() if AREA_INVESTIMENTOS not in c.zonas_atuacao]
            veio_do_fallback = True

        if not candidatos:
            return None

        escolhido = min(candidatos, key=lambda c: (self._agenda.contar_agendamentos_ativos(c.id), c.nome))
        return escolhido

    def _redigir_resposta(
        self, tipo: str, horario_texto: str, corretor: Corretor | None, especialista_investimento: bool = False
    ) -> str:
        tipo_texto = "visita" if tipo == "visita" else "reunião"
        if corretor:
            especialidade = "especialista em investimentos imobiliários" if especialista_investimento else "especialista na região"
            return (
                f"Perfeito! Vou te colocar com o(a) corretor(a) {corretor.nome}, "
                f"{especialidade} — ele(a) tem uma {tipo_texto} disponível "
                f"{horario_texto}. {PERGUNTA_CONFIRMACAO_HORARIO} Se preferir "
                "outro dia ou horário, é só me falar que eu ajusto."
            )
        return (
            f"Perfeito! Consigo encaixar uma {tipo_texto} {horario_texto}. "
            f"{PERGUNTA_CONFIRMACAO_HORARIO} Se preferir outro dia ou horário, é só me "
            "falar que eu ajusto."
        )

    @staticmethod
    def _escolher_horario(estado: EstadoConversa) -> dict:
        # Regra simples e determinística (fácil de entender e testar). Poderia
        # evoluir para checar a disponibilidade real do corretor escolhido.
        indice = abs(hash(estado.get("lead_id", ""))) % len(_HORARIOS_SUGERIDOS)
        return _HORARIOS_SUGERIDOS[indice]

    @staticmethod
    def _calcular_data_hora(opcao_horario: dict) -> datetime:
        base = datetime.utcnow() + timedelta(days=opcao_horario["dias"])
        return base.replace(hour=opcao_horario["hora"], minute=0, second=0, microsecond=0)
