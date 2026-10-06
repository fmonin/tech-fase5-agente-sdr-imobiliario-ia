"""Painel "Meu desempenho" do corretor: funil de vendas e indicadores.

Funil: Leads da área → Visitas agendadas → Visitas realizadas → Propostas
→ Negócios fechados. Os números saem da base de agenda (resultado de cada
visita registrado pelo corretor) e da carteira de leads — tudo calculado
em Python.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from src.domain.entities import Corretor
from src.domain.feedback_visita import classificar_feedback
from src.domain.interfaces import IAgendaRepository
from src.services.carteira_corretor_service import CarteiraCorretorService


@dataclass
class Desempenho:
    leads_da_area: int
    visitas_agendadas: int
    visitas_realizadas: int
    propostas: int
    fechados: int
    no_show: int
    cancelados_cliente: int
    cancelados_corretor: int
    remarcados: int
    sem_resultado: int  # visitas que já passaram sem resultado registrado
    motivos_perda: dict[str, int] = field(default_factory=dict)
    feedbacks: dict[str, int] = field(default_factory=dict)  # positivo/negativo/neutro
    valor_fechado: float = 0.0  # soma dos negócios fechados (VGV)
    propostas_em_aberto: int = 0
    captacoes: int = 0  # avaliações de imóveis de clientes (venda/locação)

    @property
    def funil(self) -> list[tuple[str, int]]:
        return [
            ("Leads da área", self.leads_da_area),
            ("Visitas agendadas", self.visitas_agendadas),
            ("Visitas realizadas", self.visitas_realizadas),
            ("Propostas", self.propostas),
            ("Negócios fechados", self.fechados),
        ]

    @staticmethod
    def _taxa(parte: int, todo: int) -> float | None:
        return parte / todo if todo else None

    @property
    def taxa_visita_proposta(self) -> float | None:
        return self._taxa(self.propostas, self.visitas_realizadas)

    @property
    def taxa_fechamento(self) -> float | None:
        return self._taxa(self.fechados, self.visitas_realizadas)

    @property
    def taxa_comparecimento(self) -> float | None:
        return self._taxa(self.visitas_realizadas, self.visitas_realizadas + self.no_show)

    def resumo_texto(self) -> str:
        def pct(v):
            return "—" if v is None else f"{v * 100:.0f}%"

        linhas = [
            "📊 **Seu desempenho**",
            " → ".join(f"{nome}: {n}" for nome, n in self.funil),
            f"Visita → proposta: {pct(self.taxa_visita_proposta)} · Fechamento: {pct(self.taxa_fechamento)} · "
            f"Comparecimento: {pct(self.taxa_comparecimento)}",
            f"Cancelamentos: {self.cancelados_cliente} pelo cliente, {self.cancelados_corretor} por você · "
            f"Remarcações: {self.remarcados}",
        ]
        if self.fechados or self.propostas_em_aberto:
            from src.domain.investimento import formatar_moeda

            linhas.append(f"Negócios fechados: {formatar_moeda(self.valor_fechado)} · "
                          f"Propostas em aberto: {self.propostas_em_aberto}")
        if self.captacoes:
            linhas.append(f"Captações (avaliações de imóveis de clientes): {self.captacoes}")
        if self.motivos_perda:
            linhas.append("Por que não fecharam: " + ", ".join(f"{m} ({n})" for m, n in self.motivos_perda.items()))
        if self.sem_resultado:
            linhas.append(f"⚠️ {self.sem_resultado} visita(s) já realizada(s) sem resultado registrado.")
        return "\n".join(linhas)


class DesempenhoCorretorService:
    def __init__(self, agenda_repository: IAgendaRepository, carteira: CarteiraCorretorService) -> None:
        self._agenda = agenda_repository
        self._carteira = carteira

    def calcular(self, corretor: Corretor) -> Desempenho:
        todos = self._agenda.listar_por_corretor(corretor.id)
        agendamentos = [a for a in todos if a.tipo != "avaliacao"]
        agora = datetime.now()
        resultados = Counter(a.resultado_visita for a in agendamentos if a.resultado_visita)
        realizadas = sum(n for r, n in resultados.items() if r != "nao_compareceu")
        motivos = Counter(a.motivo_resultado or "Outro" for a in agendamentos if a.resultado_visita == "nao_gostou")
        for a in agendamentos:  # feedback negativo do cliente sem resultado registrado também conta
            if a.feedback_cliente and a.resultado_visita is None:
                sentimento, motivo = classificar_feedback(a.feedback_cliente)
                if sentimento == "negativo":
                    motivos[motivo or "Outro"] += 1
        feedbacks = Counter(classificar_feedback(a.feedback_cliente)[0] for a in agendamentos if a.feedback_cliente)
        leads_com_agenda = {a.lead_id for a in agendamentos}
        sem_visita = {c.lead.id for c in self._carteira.clientes_sem_visita(corretor)}
        return Desempenho(
            leads_da_area=len(leads_com_agenda | sem_visita),
            # Propostas de horário trocadas pelo próprio fluxo (canceladas sem autor) não contam.
            visitas_agendadas=sum(1 for a in agendamentos if a.status == "confirmado" or a.cancelado_por
                                  or a.resultado_visita),
            visitas_realizadas=realizadas,
            propostas=resultados["proposta"] + resultados["fechado"],
            fechados=resultados["fechado"],
            no_show=resultados["nao_compareceu"],
            cancelados_cliente=sum(1 for a in agendamentos if a.status == "cancelado" and a.cancelado_por == "cliente"),
            cancelados_corretor=sum(1 for a in agendamentos if a.status == "cancelado" and a.cancelado_por == "corretor"),
            remarcados=sum(1 for a in agendamentos if a.horario_anterior),
            sem_resultado=sum(1 for a in agendamentos if a.status == "confirmado" and not a.resultado_visita
                              and a.data_hora and a.data_hora < agora),
            motivos_perda=dict(motivos.most_common()),
            feedbacks=dict(feedbacks),
            valor_fechado=sum(a.valor_negociado or 0 for a in agendamentos if a.resultado_visita == "fechado"),
            propostas_em_aberto=resultados["proposta"],
            captacoes=sum(1 for a in todos if a.tipo == "avaliacao" and a.status != "cancelado"),
        )
