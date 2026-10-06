"""Serviço de agenda do corretor (casos de uso da "Área do Corretor").

Responsabilidade: CANCELAR um agendamento a pedido do corretor, mantendo
consistentes os três lugares onde o agendamento aparece:
    1. a base de agenda (`IAgendaRepository`) — consultada pelo corretor;
    2. o histórico do lead (`ILeadRepository`) — exibido no chat/painel;
    3. o CRM simulado (`ICRM`) — trilha de auditoria.

O cliente NÃO é avisado aqui: o agendamento fica marcado como
"cancelado pelo corretor, cliente ainda não avisado" e o
`IdentificacaoAgent` dá a notícia na próxima vez que o cliente se
identificar (pelo CPF), oferecendo um novo horário.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.domain.entities import Agendamento
from src.domain.interfaces import ICRM, IAgendaRepository, ILeadRepository, IObservador


def texto_aviso_ao_corretor(a: Agendamento) -> str:
    tipo = a.tipo_texto
    cliente = a.cliente_nome or "Um cliente"
    imovel = f" ({a.imovel_titulo})" if a.imovel_titulo else ""
    if a.status == "cancelado":
        return f"❌ {cliente} cancelou a {tipo} do {a.quando_formatado()}{imovel}."
    if a.tipo == "avaliacao" and not a.horario_anterior:
        detalhes = f"\n   {a.detalhes.splitlines()[0]}" if a.detalhes else ""
        return f"🏷️ Nova captação: {cliente} quer anunciar um imóvel — {a.imovel_titulo}. Avaliação {a.quando_formatado()}.{detalhes}"
    if a.feedback_cliente and a.pos_visita_enviado:
        return f"💬 {cliente} sobre a {tipo} do {a.quando_formatado()}{imovel}: “{a.feedback_cliente}”"
    return f"🔁 {cliente} remarcou a {tipo}{imovel} do {a.horario_anterior} para {a.quando_formatado()}."


def mensagem_ao_cliente(a: Agendamento) -> str:
    """O que o Sr. Agim diz ao cliente quando o CORRETOR cancela ou remarca."""
    from src.agents.intencao_agendamento import PERGUNTA_CONFIRMACAO_HORARIO

    nome = (a.cliente_nome or "").split(" ")[0]
    oi = f"Olá, {nome}!" if nome else "Olá!"
    corretor = f"o(a) corretor(a) {a.corretor_nome}" if a.corretor_nome else "o corretor"
    imovel = f" ({a.imovel_titulo})" if a.imovel_titulo else ""
    if a.status == "cancelado":
        motivo = f" Motivo: {a.motivo_cancelamento}." if a.motivo_cancelamento else ""
        return (f"⚠️ {oi} Infelizmente {corretor} precisou cancelar a sua {a.tipo_texto} do "
                f"{a.horario_anterior or a.quando_formatado()}{imovel}.{motivo} Peço desculpas pelo transtorno! "
                "Quer que eu agende um novo horário? É só me dizer o dia e o horário (ex.: \"sexta às 15h\").")
    motivo = f" Motivo: {a.motivo_alteracao}." if a.motivo_alteracao else ""
    return (f"⚠️ {oi} {corretor[0].upper() + corretor[1:]} precisou remarcar a sua {a.tipo_texto}{imovel} do "
            f"{a.horario_anterior} para {a.quando_formatado()}.{motivo} {PERGUNTA_CONFIRMACAO_HORARIO} "
            "Se preferir outro dia ou horário, é só me falar que eu ajusto.")


@dataclass
class ResultadoCancelamento:
    sucesso: bool
    mensagem: str
    agendamento: Optional[Agendamento] = None
    # Como o cliente ficou sabendo: "telegram" (na hora), "chat" (mensagem no
    # histórico; também é avisado ao se identificar) ou "" (não deu para avisar).
    cliente_avisado_por: str = ""


class AgendaService:
    def __init__(
        self,
        agenda_repository: IAgendaRepository,
        lead_repository: ILeadRepository,
        crm: ICRM,
        observador: Optional[IObservador] = None,
        avisar_cliente=None,  # Callable[[lead_id, texto], bool] — ex.: envio pelo Telegram
    ) -> None:
        self._avisar_cliente = avisar_cliente
        self._agenda = agenda_repository
        self._leads = lead_repository
        self._crm = crm
        self._observador = observador

    def agendamentos_ativos(self, corretor_id: str) -> list[Agendamento]:
        """Agendamentos não cancelados do corretor, do mais próximo ao mais distante."""
        ativos = [a for a in self._agenda.listar_por_corretor(corretor_id) if a.status != "cancelado"]
        return sorted(ativos, key=lambda a: a.data_hora or datetime.max)

    def avisos_do_cliente_para_corretor(self, corretor_id: str) -> list[str]:
        """Cancelamentos/remarcações feitos pelo CLIENTE que o corretor ainda
        não viu. Devolve os textos do aviso e marca como vistos (o aviso
        aparece uma única vez, como acontece com o cliente)."""
        avisos = []
        for a in self._agenda.listar_avisos_corretor(corretor_id):
            avisos.append(texto_aviso_ao_corretor(a))
            a.corretor_notificado = True
            self._agenda.registrar(a)
            if self._observador:
                self._observador.registrar_evento(
                    "corretor_avisado", {"corretor_id": corretor_id, "agendamento_id": a.id, "status": a.status}
                )
        return avisos

    def cancelar_pelo_corretor(
        self, agendamento_id: str, corretor_id: str, motivo: Optional[str] = None
    ) -> ResultadoCancelamento:
        agendamento = self._agenda.buscar_por_id(agendamento_id)
        if agendamento is None:
            return ResultadoCancelamento(False, "Agendamento não encontrado.")
        if agendamento.corretor_id != corretor_id:
            return ResultadoCancelamento(False, "Esse agendamento não é seu — só o corretor responsável pode cancelá-lo.")
        if agendamento.status == "cancelado":
            return ResultadoCancelamento(False, "Esse agendamento já estava cancelado.", agendamento)

        agendamento.status = "cancelado"
        agendamento.confirmado = False
        agendamento.cancelado_por = "corretor"
        agendamento.motivo_cancelamento = (motivo or "").strip() or None
        agendamento.cancelado_em = datetime.utcnow()
        agendamento.cliente_notificado = False

        self._agenda.registrar(agendamento)
        self._crm.registrar_agendamento(agendamento)
        self._atualizar_historico_do_lead(agendamento)
        if self._observador:
            self._observador.registrar_evento(
                "agendamento_cancelado_pelo_corretor",
                {"agendamento_id": agendamento.id, "corretor_id": corretor_id, "lead_id": agendamento.lead_id},
            )
        return ResultadoCancelamento(True, "Agendamento cancelado.", agendamento, self._notificar_cliente(agendamento))

    def reagendar_pelo_corretor(
        self,
        agendamento_id: str,
        corretor_id: str,
        nova_data_hora: datetime,
        novo_texto: str,
        motivo: Optional[str] = None,
    ) -> ResultadoCancelamento:
        """Remarca para outro dia/horário. O agendamento volta a ficar
        "sugerido": o cliente é avisado ao se identificar e pode confirmar
        o novo horário (ou pedir outro) direto no chat com o Sr. Agim."""
        agendamento = self._agenda.buscar_por_id(agendamento_id)
        if agendamento is None:
            return ResultadoCancelamento(False, "Agendamento não encontrado.")
        if agendamento.corretor_id != corretor_id:
            return ResultadoCancelamento(False, "Esse agendamento não é seu — só o corretor responsável pode alterá-lo.")
        if agendamento.status == "cancelado":
            return ResultadoCancelamento(False, "Esse agendamento está cancelado e não pode ser remarcado.", agendamento)

        texto_anterior = agendamento.quando_sugerido
        agendamento.horario_anterior = agendamento.quando_formatado()
        agendamento.data_hora = nova_data_hora
        agendamento.quando_sugerido = novo_texto
        agendamento.status = "sugerido"
        agendamento.confirmado = False
        agendamento.motivo_alteracao = (motivo or "").strip() or None
        agendamento.cliente_notificado = False

        self._agenda.registrar(agendamento)
        self._crm.registrar_agendamento(agendamento)
        self._atualizar_historico_do_lead(agendamento, texto_anterior)
        if self._observador:
            self._observador.registrar_evento(
                "agendamento_remarcado_pelo_corretor",
                {"agendamento_id": agendamento.id, "corretor_id": corretor_id, "novo_horario": novo_texto},
            )
        return ResultadoCancelamento(True, "Agendamento remarcado.", agendamento, self._notificar_cliente(agendamento))

    def _notificar_cliente(self, agendamento: Agendamento) -> str:
        """Avisa o cliente NA HORA: a mensagem entra no histórico do chat dele e,
        se ele conversa pelo Telegram, chega no celular. Se não houver Telegram,
        o aviso também aparece quando ele se identificar (`cliente_notificado`
        continua False)."""
        texto = mensagem_ao_cliente(agendamento)
        lead = self._leads.buscar_por_id(agendamento.lead_id) if agendamento.lead_id else None
        if lead is None and agendamento.cliente_cpf:
            lead = self._leads.buscar_por_cpf(agendamento.cliente_cpf)
        avisado_por = ""
        if lead is not None:
            from src.domain.entities import RemetenteMensagem

            lead.registrar_mensagem(RemetenteMensagem.AGENTE, texto)
            lead.atendimento_encerrado = False
            self._leads.salvar(lead)
            avisado_por = "chat"
        if self._avisar_cliente and lead is not None:
            try:
                if self._avisar_cliente(lead.id, texto):
                    avisado_por = "telegram"
            except Exception:  # noqa: BLE001 — sem Telegram, o aviso fica no chat e no login
                pass
        if avisado_por == "telegram":
            agendamento.cliente_notificado = True
            self._agenda.registrar(agendamento)
        if self._observador:
            self._observador.registrar_evento(
                "cliente_avisado_pelo_corretor", {"agendamento_id": agendamento.id, "canal": avisado_por or "nenhum"})
        return avisado_por

    def conflitos(self, corretor_id: str, data_hora: datetime, ignorar_id: str = "") -> list[Agendamento]:
        """Outros compromissos ativos do corretor no mesmo horário."""
        return [
            a
            for a in self.agendamentos_ativos(corretor_id)
            if a.id != ignorar_id and a.data_hora and abs((a.data_hora - data_hora).total_seconds()) < 3600
        ]

    def _atualizar_historico_do_lead(self, alterado: Agendamento, texto_anterior: Optional[str] = None) -> None:
        lead = self._leads.buscar_por_id(alterado.lead_id)
        if lead is None:
            return
        texto_busca = texto_anterior or alterado.quando_sugerido
        for a in lead.agendamentos:
            # O agendamento do histórico do lead pode ter outro id (é criado
            # pelo ConversationService), então casamos também pelo horário.
            if a.id == alterado.id or (a.quando_sugerido == texto_busca and a.status != "cancelado"):
                a.status = alterado.status
                a.confirmado = alterado.confirmado
                a.quando_sugerido = alterado.quando_sugerido
                a.data_hora = alterado.data_hora
                a.cancelado_por = alterado.cancelado_por
                a.motivo_cancelamento = alterado.motivo_cancelamento
                a.cancelado_em = alterado.cancelado_em
                a.horario_anterior = alterado.horario_anterior
                a.motivo_alteracao = alterado.motivo_alteracao
        self._leads.salvar(lead)

