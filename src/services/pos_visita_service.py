"""Pós-visita: o que acontece DEPOIS da visita — onde o negócio é fechado
ou perdido.

1. O corretor registra o resultado de cada visita (gostou, proposta,
   fechado, não gostou + motivo, não compareceu). É isso que alimenta o
   funil de vendas do "Meu desempenho". Quando o cliente não gostou, o
   motivo volta para o Sr. Agim, que passa a sugerir opções melhores
   (`src/domain/feedback_visita.py`).
2. Depois do horário da visita, o Sr. Agim pergunta ao cliente "o que achou
   da visita?" (como o follow-up: automático pelo bot do Telegram). A
   resposta é registrada e o corretor recebe um aviso na Área do Corretor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from src.domain.entities import Agendamento, Lead, RemetenteMensagem
from src.domain.feedback_visita import MOTIVOS_PERDA, RESULTADOS_VISITA, registro_de_feedback
from src.domain.interfaces import ICRM, IAgendaRepository, ILeadRepository, IObservador, IPropertyRepository

MARCADOR_POS_VISITA = "POSVISITA"
# Resultados que ainda podem evoluir (gostou -> proposta -> fechado / não fechou).
EM_NEGOCIACAO = ("gostou", "proposta")


@dataclass
class PosVisitaEnviada:
    lead: Lead
    agendamento: Agendamento
    mensagem: str


def mensagem_pos_visita(a: Agendamento, nome: Optional[str]) -> str:
    primeiro = (nome or a.cliente_nome or "").split(" ")[0]
    oi = f"Oi, {primeiro}!" if primeiro else "Oi!"
    tipo = a.tipo_texto
    onde = f" ao {a.imovel_titulo}" if a.imovel_titulo and a.tipo == "visita" else ""
    com = f" com o(a) corretor(a) {a.corretor_nome}" if a.corretor_nome else ""
    pergunta = "Gostou do imóvel?" if a.tipo == "visita" else "Ficou alguma dúvida?"
    return (f"{oi} Como foi a {tipo}{onde}{com} ({a.quando_formatado()})? {pergunta} "
            f"Me conta o que achou 🙂\n\n[[{MARCADOR_POS_VISITA}:{a.id}]]")


class PosVisitaService:
    def __init__(
        self,
        agenda_repository: IAgendaRepository,
        lead_repository: ILeadRepository,
        repositorio_imoveis: IPropertyRepository | None = None,
        crm: ICRM | None = None,
        observador: IObservador | None = None,
    ) -> None:
        self._agenda = agenda_repository
        self._leads = lead_repository
        self._imoveis = repositorio_imoveis
        self._crm = crm
        self._observador = observador

    # ------------------------------------------------------------ resultado
    def visitas_para_registrar(self, corretor_id: str) -> list[Agendamento]:
        """Visitas que o corretor pode atualizar: as que já aconteceram sem
        resultado, depois as NEGOCIAÇÕES EM ANDAMENTO (gostou/proposta — para
        marcar "proposta aceita / negócio fechado") e por último as futuras."""
        agora = datetime.now()
        itens = [
            a for a in self._agenda.listar_por_corretor(corretor_id)
            if a.status == "confirmado" and a.tipo != "avaliacao"
            and (not a.resultado_visita or a.resultado_visita in EM_NEGOCIACAO)
        ]

        def ordem(a: Agendamento):
            if a.resultado_visita in EM_NEGOCIACAO:
                return (1, a.data_hora or datetime.max)
            return (0 if a.data_hora and a.data_hora <= agora else 2, a.data_hora or datetime.max)

        return sorted(itens, key=ordem)

    def negociacoes_em_andamento(self, corretor_id: str) -> list[Agendamento]:
        return [a for a in self.visitas_para_registrar(corretor_id) if a.resultado_visita in EM_NEGOCIACAO]

    def registrar_resultado(
        self,
        agendamento_id: str,
        corretor_id: str,
        resultado: str,
        motivo: Optional[str] = None,
        observacao: Optional[str] = None,
        valor: Optional[float] = None,
    ) -> tuple[bool, str]:
        if resultado not in RESULTADOS_VISITA:
            return False, "Resultado inválido."
        a = self._agenda.buscar_por_id(agendamento_id)
        if a is None or a.corretor_id != corretor_id:
            return False, "Agendamento não encontrado na sua agenda."
        if resultado == "nao_gostou" and motivo not in MOTIVOS_PERDA:
            motivo = "Outro"
        a.resultado_visita = resultado
        a.motivo_resultado = motivo if resultado == "nao_gostou" else None
        a.observacao_resultado = (observacao or "").strip() or a.observacao_resultado
        if valor:
            a.valor_negociado = float(valor)
        a.resultado_em = datetime.utcnow()
        self._agenda.registrar(a)
        if self._crm:
            self._crm.registrar_agendamento(a)
        if resultado == "nao_gostou":
            self._guardar_feedback_no_lead(a, motivo)
        if self._observador:
            self._observador.registrar_evento(
                "resultado_visita_registrado",
                {"agendamento_id": a.id, "corretor_id": corretor_id, "resultado": resultado, "motivo": motivo},
            )
        texto = f"Resultado registrado: {RESULTADOS_VISITA[resultado]}"
        if resultado in ("proposta", "fechado") and a.valor_negociado:
            from src.domain.investimento import formatar_moeda

            texto += f" — valor: {formatar_moeda(a.valor_negociado)}"
        if resultado == "fechado":
            texto += ". Parabéns pelo negócio! 🎉 Já entrou no seu desempenho"
        if resultado == "nao_gostou":
            texto += f" (motivo: {motivo}). O {_agente()} vai considerar isso nas próximas sugestões ao cliente."
        return True, texto

    def guardar_feedback_do_cliente(self, a: Agendamento, motivo: Optional[str]) -> dict:
        """Registro do feedback negativo (usado também pelo chat do cliente)."""
        imovel = self._imovel(a.imovel_id)
        return registro_de_feedback(imovel, motivo, a.imovel_id)

    def _guardar_feedback_no_lead(self, a: Agendamento, motivo: Optional[str]) -> None:
        lead = self._leads.buscar_por_id(a.lead_id) if a.lead_id else None
        if lead is None or not a.imovel_id:
            return
        registro = registro_de_feedback(self._imovel(a.imovel_id), motivo, a.imovel_id)
        if not any(f.get("imovel_id") == registro["imovel_id"] for f in lead.feedback_visitas):
            lead.feedback_visitas.append(registro)
            self._leads.salvar(lead)

    def _imovel(self, imovel_id: Optional[str]):
        if not imovel_id or self._imoveis is None:
            return None
        return next((im for im in self._imoveis.listar_todos() if im.id == imovel_id), None)

    # ------------------------------------------------------------ pós-visita
    def enviar_pos_visita(self, horas_apos: float = 2, lead_id: Optional[str] = None) -> list[PosVisitaEnviada]:
        """Pergunta "o que achou?" aos clientes cujas visitas confirmadas já
        aconteceram há `horas_apos` horas. Com `lead_id`, força para a visita
        mais recente desse cliente (comando /posvisita, demonstração)."""
        limite = datetime.now() - timedelta(hours=horas_apos)
        enviados: list[PosVisitaEnviada] = []
        candidatos: list[Agendamento] = []
        if lead_id:
            lead = self._leads.buscar_por_id(lead_id)
            if lead and lead.cpf:
                confirmados = [a for a in self._agenda.listar_por_cliente(lead.cpf)
                               if a.status == "confirmado" and not a.feedback_cliente and a.tipo != "avaliacao"]
                candidatos = sorted(confirmados, key=lambda a: a.data_hora or datetime.min)[-1:]
        else:
            for lead in self._leads.listar_todos():
                if not lead.cpf:
                    continue
                candidatos += [
                    a for a in self._agenda.listar_por_cliente(lead.cpf)
                    if a.status == "confirmado" and not a.pos_visita_enviado and a.tipo != "avaliacao"
                    and a.data_hora and a.data_hora <= limite
                    and a.resultado_visita != "nao_compareceu"
                ]
        for a in {c.id: c for c in candidatos}.values():
            lead = self._leads.buscar_por_id(a.lead_id) if a.lead_id else None
            if lead is None and a.cliente_cpf:
                lead = self._leads.buscar_por_cpf(a.cliente_cpf)
            if lead is None:
                continue
            mensagem = mensagem_pos_visita(a, lead.nome)
            lead.registrar_mensagem(RemetenteMensagem.AGENTE, mensagem)
            self._leads.salvar(lead)
            a.pos_visita_enviado = True
            self._agenda.registrar(a)
            if self._observador:
                self._observador.registrar_evento("pos_visita_enviado", {"lead_id": lead.id, "agendamento_id": a.id})
            enviados.append(PosVisitaEnviada(lead, a, mensagem))
        return enviados


def _agente() -> str:
    from src.config import settings

    return settings.agente_nome
