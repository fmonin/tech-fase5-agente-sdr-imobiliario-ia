"""Orquestração multiagente com LangGraph.

Este é o "cérebro" do Sr. Agim (Agente SDR): um grafo de estados onde cada nó é um
agente especializado (Single Responsibility Principle) e as arestas
condicionais formam um **supervisor/roteador** que decide qual agente deve
agir a seguir, dependendo do que já se sabe sobre o lead.

Fluxo (uma execução do grafo = processar UMA mensagem do lead):

    entrada
       │
       ▼
  (roteador de entrada) ── cliente já identificado (nome+CPF)?
       │                                    │
      não                                  sim
       │                                    │
       ▼                                    ▼
 [Identificação] ── FIM             [Qualificador] ── sempre roda: extrai/
 (pergunta CPF/nome,                atualiza dados do lead
  ou recupera histórico                     │
  de cliente recorrente)                    ▼
                                     (roteador) ──┬─────────────┬───────────┐
                                         │         │             │           │
                                   dados        quer agendar/  dados      (fallback)
                                   incompletos  já qualificado  completos
                                         │       o bastante        │
                                         ▼            │            ▼
                                  [Esclarecedor]       │    [Consultor de Imóveis]
                                         │             ▼            │
                                         │       [Agendador]        │
                                         │             │            │
                                         │             ▼            │
                                         │       [Resumidor] (se    │
                                         │        lead ficou        │
                                         │        "quente")         │
                                         │             │            │
                                         └─────────────┴────────────┘
                                                        ▼
                                                       FIM

Por que um supervisor/roteador (e não um grafo linear fixo)?
    Porque a conversa real não segue sempre a mesma ordem — às vezes o lead
    já chega querendo agendar, às vezes precisa de várias perguntas antes.
    O roteador (`_rotear_apos_qualificacao` / `_rotear_apos_imoveis`) é o
    que dá o caráter "multiagente" de verdade ao sistema: agentes
    colaborando e se revezando, não um pipeline rígido. O roteador de
    ENTRADA (`_rotear_entrada`) segue o mesmo espírito: antes de qualquer
    qualificação, o sistema precisa saber QUEM está falando (requisito de
    cadastro de cliente) — veja `src/agents/identification_agent.py`.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.clarifier_agent import EsclarecedorAgent
from src.agents.agenda_cliente_agent import AgendaClienteAgent
from src.agents.captacao_agent import CaptacaoImovelAgent
from src.agents.encerramento_agent import EncerramentoAgent, aguardando_nota, nota_do_atendimento, quer_encerrar
from src.agents.identification_agent import IdentificacaoAgent
from src.agents.property_agent import ConsultorImoveisAgent
from src.agents.property_detail_agent import DetalheImovelAgent
from src.agents.qualifier_agent import QualificadorAgent
from src.agents.recap_agent import RecapituladorAgent
from src.agents.scheduler_agent import AgendadorAgent
from src.agents.state import EstadoConversa
from src.agents.summarizer_agent import ResumidorAgent
from src.domain.localizacao import MapaBairros
from src.domain.interfaces import (
    ICRM,
    IAgendaRepository,
    ICorretorRepository,
    ILeadRepository,
    ILLMProvider,
    IMarketDataRepository,
    IPropertyRepository,
    IVectorSearch,
)


def construir_grafo_sdr(
    llm_provider: ILLMProvider,
    repositorio_imoveis: IPropertyRepository,
    busca_semantica: IVectorSearch,
    crm: ICRM,
    lead_repository: ILeadRepository,
    corretor_repository: ICorretorRepository,
    agenda_repository: IAgendaRepository,
    dados_mercado: IMarketDataRepository | None = None,
    mapa_bairros: MapaBairros | None = None,
):
    """Monta e compila o grafo multiagente.

    Cada dependência é injetada de fora (Dependency Injection manual) —
    isto é o que permite, por exemplo, testar o grafo inteiro com um
    `MockLLMProvider` e um CRM falso, sem nenhuma chamada de rede.
    """
    identificacao = IdentificacaoAgent(lead_repository, agenda_repository, corretor_repository)
    qualificador = QualificadorAgent(llm_provider)
    esclarecedor = EsclarecedorAgent(llm_provider)
    consultor_imoveis = ConsultorImoveisAgent(
        llm_provider, repositorio_imoveis, busca_semantica, dados_mercado, mapa_bairros
    )
    detalhe_imovel = DetalheImovelAgent(llm_provider, repositorio_imoveis, dados_mercado, mapa_bairros)
    agendador = AgendadorAgent(
        llm_provider, crm, corretor_repository, agenda_repository, repositorio_imoveis
    )
    resumidor = ResumidorAgent(llm_provider, crm)
    recapitulador = RecapituladorAgent(llm_provider)

    grafo = StateGraph(EstadoConversa)

    grafo.add_node("identificacao", identificacao)
    grafo.add_node("encerramento", EncerramentoAgent(agenda_repository))
    grafo.add_node("agenda_cliente", AgendaClienteAgent(agenda_repository, corretor_repository, crm, repositorio_imoveis))
    grafo.add_node("captacao_imovel", CaptacaoImovelAgent(
        agenda_repository, corretor_repository, repositorio_imoveis, mapa_bairros, dados_mercado, crm))
    grafo.add_node("qualificador", qualificador)
    grafo.add_node("esclarecedor", esclarecedor)
    grafo.add_node("consultor_imoveis", consultor_imoveis)
    grafo.add_node("detalhe_imovel", detalhe_imovel)
    grafo.add_node("agendador", agendador)
    grafo.add_node("resumidor", resumidor)
    grafo.add_node("recapitulador", recapitulador)

    grafo.set_conditional_entry_point(
        _rotear_entrada,
        {"identificacao": "identificacao", "agenda_cliente": "agenda_cliente", "encerramento": "encerramento"},
    )

    # Cliente identificado: primeiro vê se é sobre a agenda DELE (consultar,
    # cancelar, remarcar); se não for, a conversa segue para o Qualificador.
    grafo.add_conditional_edges(
        "agenda_cliente",
        lambda estado: "fim" if estado.get("agenda_cliente_respondeu") else "captacao_imovel",
        {"fim": END, "captacao_imovel": "captacao_imovel"},
    )

    # Quer VENDER ou colocar para ALUGAR o próprio imóvel? (captação)
    grafo.add_conditional_edges(
        "captacao_imovel",
        lambda estado: "fim" if estado.get("captacao_respondeu") else "qualificador",
        {"fim": END, "qualificador": "qualificador"},
    )

    grafo.add_conditional_edges(
        "qualificador",
        _rotear_apos_qualificacao,
        {
            "esclarecedor": "esclarecedor",
            "agendador": "agendador",
            "consultor_imoveis": "consultor_imoveis",
            "detalhe_imovel": "detalhe_imovel",
            "recapitulador": "recapitulador",
        },
    )

    # Detalhes de um imóvel: se não deu para saber QUAL imóvel, segue para o
    # Consultor (busca normal); se deu, a resposta já está pronta.
    grafo.add_conditional_edges(
        "detalhe_imovel",
        lambda estado: "fim" if estado.get("imovel_detalhado") else "consultor_imoveis",
        {"fim": END, "consultor_imoveis": "consultor_imoveis"},
    )

    grafo.add_conditional_edges(
        "consultor_imoveis",
        _rotear_apos_imoveis,
        {"agendador": "agendador", "fim": END},
    )

    grafo.add_conditional_edges(
        "agendador",
        _rotear_apos_agendamento,
        {"resumidor": "resumidor", "fim": END},
    )

    grafo.add_edge("identificacao", END)
    grafo.add_edge("encerramento", END)
    grafo.add_edge("esclarecedor", END)
    grafo.add_edge("resumidor", END)
    grafo.add_edge("recapitulador", END)

    return grafo.compile()


# --- Funções de roteamento (o "supervisor" do sistema multiagente) --------


def _rotear_entrada(estado: EstadoConversa) -> str:
    # O cliente pode encerrar o atendimento a qualquer momento (e dar a nota).
    mensagem = estado.get("mensagem_usuario", "")
    if quer_encerrar(mensagem) or (aguardando_nota(estado) and nota_do_atendimento(mensagem)):
        return "encerramento"
    if estado.get("cliente_identificado"):
        return "agenda_cliente"
    return "identificacao"


def _rotear_apos_qualificacao(estado: EstadoConversa) -> str:
    # "O que já conversamos?" — o cliente quer relembrar antes de seguir.
    if estado.get("pediu_resumo"):
        return "recapitulador"
    if estado.get("quer_agendar") and estado.get("temperatura") in ("morno", "quente"):
        return "agendador"
    # "Quero mais informação do imóvel do Tatuapé": ficha do imóvel, em vez
    # de refazer a busca (vale mesmo com o perfil ainda incompleto).
    if estado.get("pediu_detalhes"):
        return "detalhe_imovel"
    if not estado.get("dados_completos"):
        return "esclarecedor"
    return "consultor_imoveis"


def _rotear_apos_imoveis(estado: EstadoConversa) -> str:
    if estado.get("quer_agendar"):
        return "agendador"
    return "fim"


def _rotear_apos_agendamento(estado: EstadoConversa) -> str:
    # Um agendamento confirmado é o sinal ideal para preparar o handoff
    # para o corretor humano com um resumo pronto.
    if estado.get("agendamento_status") == "confirmado" or estado.get("temperatura") == "quente":
        return "resumidor"
    return "fim"
