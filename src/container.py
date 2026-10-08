"""Composition Root (raiz de composição da injeção de dependências).

Este é o ÚNICO arquivo do projeto que conhece TODAS as classes concretas
(Azure, Mock, SQLite, JSON...) e as conecta. Todo o resto do sistema
(agentes, serviços, interface) só enxerga interfaces (`src/domain/interfaces.py`).

Para um estudante iniciante:
    "Composition root" é só um nome bonito para "o lugar onde eu crio os
    objetos de verdade e entrego (injeto) eles para quem precisa". Manter
    isso centralizado em um único arquivo é o que torna o projeto fácil de
    testar e de estender — quer trocar o CRM simulado por um de verdade?
    Só mexe aqui.
"""
from __future__ import annotations

import logging
from pathlib import Path
from dataclasses import dataclass

from src.agents.agenda_query_agent import ConsultaAgendaAgent
from src.agents.followup_agent import FollowUpAgent
from src.agents.summarizer_agent import ResumidorAgent
from src.agents.gestao_agenda_agent import GestaoAgendaCorretorAgent
from src.agents.graph import construir_grafo_sdr
from src.config import settings
from src.domain.interfaces import IAgendaRepository, ICorretorRepository, IPropertyRepository
from src.infrastructure.crm.mock_crm import MockCRM
from src.infrastructure.llm.factory import criar_llm_provider
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
from src.infrastructure.mercado.json_market_data_repository import JsonMarketDataRepository
from src.infrastructure.observability.logger import EventoStore, configurar_logging
from src.infrastructure.rag.factory import criar_busca_semantica
from src.infrastructure.repositories.json_mapa_bairros import carregar_mapa_bairros
from src.infrastructure.repositories.sqlite_corretor_repository import SqliteCorretorRepository
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository
from src.services.conversation_service import ConversationService
from src.services.agenda_service import AgendaService
from src.agents.menu_corretor_agent import MenuCorretorAgent
from src.services.carteira_corretor_service import CarteiraCorretorService
from src.services.desempenho_corretor_service import DesempenhoCorretorService
from src.services.match_imoveis_service import MatchImoveisService
from src.services.pos_visita_service import PosVisitaService
from src.services.captacao_service import CaptacaoService
from src.services.argumentos_followup import ArgumentosFollowUp
from src.infrastructure.voice.azure_speech_service import criar_servico_de_voz
from src.domain.interfaces import IVoiceService
from typing import Optional
from src.services.painel_gestor_service import PainelGestorService
from src.services.dashboard_service import DashboardService


@dataclass
class AppContainer:
    """Agrupa todos os serviços prontos para uso pela interface."""

    conversation_service: ConversationService
    dashboard_service: DashboardService
    evento_store: EventoStore
    corretor_repository: ICorretorRepository
    agenda_repository: IAgendaRepository
    agenda_query_agent: ConsultaAgendaAgent
    agenda_service: AgendaService
    gestao_agenda_agent: GestaoAgendaCorretorAgent
    repositorio_imoveis: IPropertyRepository
    followup_agent: FollowUpAgent
    carteira_corretor: CarteiraCorretorService
    pos_visita: PosVisitaService
    match_imoveis: MatchImoveisService
    desempenho_corretor: DesempenhoCorretorService
    menu_corretor: MenuCorretorAgent
    painel_gestor: PainelGestorService
    captacao: CaptacaoService
    voz: Optional[IVoiceService] = None  # Azure Speech (só com AZURE_SPEECH_KEY)


def montar_container() -> AppContainer:
    configurar_logging(settings.log_level)

    llm_provider = criar_llm_provider(settings)
    logging.getLogger("agente_sdr").info("LLM ativo: %s", settings.descricao_llm)
    repositorio_imoveis = SqlitePropertyRepository(
        caminho_db=settings.imoveis_database_path,
        caminho_seed_json=settings.imoveis_seed_json_path,
    )
    mapa_bairros = carregar_mapa_bairros(settings.bairros_json_path)
    busca_semantica = criar_busca_semantica(repositorio_imoveis, mapa_bairros)
    # O CRM simulado fica ao lado do banco: em testes (banco temporário) ele
    # também é temporário e nunca toca o data/crm_simulado.json real.
    crm = MockCRM(Path(settings.database_path).parent / "crm_simulado.json")
    lead_repository = SqliteLeadRepository(settings.database_path)
    corretor_repository = SqliteCorretorRepository(caminho_db=settings.database_path)
    agenda_repository = SqliteAgendaRepository(caminho_db=settings.database_path)
    evento_store = EventoStore(settings.database_path)
    dados_mercado = JsonMarketDataRepository(settings.mercado_json_path)

    grafo = construir_grafo_sdr(
        llm_provider=llm_provider,
        repositorio_imoveis=repositorio_imoveis,
        busca_semantica=busca_semantica,
        crm=crm,
        lead_repository=lead_repository,
        corretor_repository=corretor_repository,
        agenda_repository=agenda_repository,
        dados_mercado=dados_mercado,
        mapa_bairros=mapa_bairros,
    )

    conversation_service = ConversationService(
        grafo_compilado=grafo,
        lead_repository=lead_repository,
        crm=crm,
        observador=evento_store,
        resumidor=ResumidorAgent(llm_provider, crm),
    )
    dashboard_service = DashboardService(lead_repository, evento_store)
    agenda_query_agent = ConsultaAgendaAgent(llm_provider, agenda_repository)
    agenda_service = AgendaService(
        agenda_repository, lead_repository, crm, evento_store,
        avisar_cliente=_enviar_telegram_ao_lead if settings.telegram_habilitado else None,
    )
    gestao_agenda_agent = GestaoAgendaCorretorAgent(llm_provider, agenda_service, agenda_query_agent)

    carteira = CarteiraCorretorService(
        lead_repository, repositorio_imoveis, agenda_repository, mapa_bairros, dados_mercado
    )
    pos_visita = PosVisitaService(agenda_repository, lead_repository, repositorio_imoveis, crm, evento_store)
    match_imoveis = MatchImoveisService(
        repositorio_imoveis, lead_repository, mapa_bairros, dados_mercado,
        enviar_telegram=_enviar_telegram_ao_lead if settings.telegram_habilitado else None,
        observador=evento_store,
    )
    desempenho = DesempenhoCorretorService(agenda_repository, carteira)
    captacao = CaptacaoService(agenda_repository, lead_repository, match_imoveis, evento_store)
    menu_corretor = MenuCorretorAgent(agenda_query_agent, carteira, pos_visita, match_imoveis, desempenho, captacao)

    return AppContainer(
        conversation_service=conversation_service,
        dashboard_service=dashboard_service,
        evento_store=evento_store,
        corretor_repository=corretor_repository,
        agenda_repository=agenda_repository,
        agenda_query_agent=agenda_query_agent,
        agenda_service=agenda_service,
        gestao_agenda_agent=gestao_agenda_agent,
        repositorio_imoveis=repositorio_imoveis,
        followup_agent=FollowUpAgent(
            llm_provider, lead_repository,
            argumentos=ArgumentosFollowUp(repositorio_imoveis, lead_repository, match_imoveis, carteira, dados_mercado),
        ),
        carteira_corretor=carteira,
        pos_visita=pos_visita,
        match_imoveis=match_imoveis,
        desempenho_corretor=desempenho,
        menu_corretor=menu_corretor,
        captacao=captacao,
        voz=criar_servico_de_voz(settings),
        painel_gestor=PainelGestorService(
            lead_repository, agenda_repository, corretor_repository, repositorio_imoveis,
            carteira, match_imoveis, desempenho, evento_store,
        ),
    )


def _enviar_telegram_ao_lead(lead_id: str, texto: str) -> bool:
    """Envia uma mensagem do Sr. Agim aos chats do Telegram do lead (se houver).
    Devolve True se havia chat para enviar."""
    from src.infrastructure.notifications.telegram_notifier import TelegramNotifier
    from src.infrastructure.notifications.telegram_sessoes import SessoesTelegram

    chats = SessoesTelegram().chats_do_lead(lead_id)  # relê o arquivo: o bot pode ter criado sessões
    if chats:
        notificador = TelegramNotifier()
        for chat_id in chats:
            notificador.enviar(chat_id, texto)
    return bool(chats)
