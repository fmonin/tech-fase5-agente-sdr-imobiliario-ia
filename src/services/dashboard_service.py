"""Serviço de aplicação: dados agregados para o dashboard do corretor.

Responsabilidade única: transformar dados brutos (leads, eventos de
observabilidade, registros do CRM) em algo pronto para a interface exibir
— sem a interface (Streamlit) precisar saber de SQLite, JSON ou como os
dados são armazenados.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.domain.entities import Lead
from src.domain.interfaces import ILeadRepository
from src.infrastructure.observability.logger import EventoStore


@dataclass
class MetricasGerais:
    total_leads: int
    leads_quentes: int
    leads_mornos: int
    leads_frios: int
    total_agendamentos: int
    total_mensagens: int


class DashboardService:
    def __init__(self, lead_repository: ILeadRepository, evento_store: EventoStore) -> None:
        self._leads = lead_repository
        self._eventos = evento_store

    def listar_leads(self) -> list[Lead]:
        return self._leads.listar_todos()

    def listar_eventos_recentes(self, limite: int = 50) -> list[dict]:
        return self._eventos.listar_eventos(limite)

    def calcular_metricas(self) -> MetricasGerais:
        leads = self.listar_leads()
        return MetricasGerais(
            total_leads=len(leads),
            leads_quentes=sum(1 for l in leads if l.perfil.temperatura.value == "quente"),
            leads_mornos=sum(1 for l in leads if l.perfil.temperatura.value == "morno"),
            leads_frios=sum(1 for l in leads if l.perfil.temperatura.value == "frio"),
            total_agendamentos=sum(len(l.agendamentos) for l in leads),
            total_mensagens=sum(len(l.historico) for l in leads),
        )
