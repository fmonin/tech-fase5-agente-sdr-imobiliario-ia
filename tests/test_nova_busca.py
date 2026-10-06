"""Regressão (03/10): cliente recorrente que falava de INVESTIMENTO diz
"não" ao convite e depois "quero comprar um imóvel na zona leste, mooca".
Antes: "quero ..." era lido como "sim" ao convite -> agendamento direto, sem
nenhuma pergunta, e o perfil antigo de investimento contaminava a busca."""
from pathlib import Path

import pytest

from src.agents.graph import construir_grafo_sdr
from src.agents.intencao_agendamento import eh_aceite
from src.infrastructure.crm.mock_crm import MockCRM
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
from src.infrastructure.rag.vector_store import TfidfVectorSearch
from src.infrastructure.repositories.sqlite_corretor_repository import SqliteCorretorRepository
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository
from src.services.conversation_service import ConversationService


class _ObservadorNulo:
    def registrar_evento(self, nome, dados):
        pass


@pytest.fixture()
def servico(tmp_path):
    imoveis = SqlitePropertyRepository(caminho_db=tmp_path / "imoveis.db", caminho_seed_json="tests/fixtures/imoveis_teste.json")
    leads = SqliteLeadRepository(tmp_path / "sdr.db")
    grafo = construir_grafo_sdr(
        llm_provider=MockLLMProvider(),
        repositorio_imoveis=imoveis,
        busca_semantica=TfidfVectorSearch(imoveis),
        crm=MockCRM(tmp_path / "crm.json"),
        lead_repository=leads,
        corretor_repository=SqliteCorretorRepository(caminho_db=tmp_path / "sdr.db", caminho_seed_json=Path("data/corretores.json")),
        agenda_repository=SqliteAgendaRepository(caminho_db=tmp_path / "sdr.db"),
    )
    return ConversationService(grafo, leads, MockCRM(tmp_path / "crm.json"), _ObservadorNulo())


@pytest.mark.parametrize(
    "texto",
    ["quero comprar um imovel na zona leste, mooca", "quero ver apartamentos de 3 quartos", "quero investir 300 mil"],
)
def test_pedido_novo_nao_e_aceite(texto):
    assert eh_aceite(texto) is False


@pytest.mark.parametrize("texto", ["sim", "Sim eu quero", "quero sim", "pode confirmar", "Pode ser este dia"])
def test_aceites_curtos(texto):
    assert eh_aceite(texto) is True


def test_troca_de_investimento_para_compra_faz_perguntas(servico):
    lead = servico.obter_ou_criar_lead(None)
    for msg in ("Sou Fernando Monin, CPF 529.982.247-25", "quero investir 500 mil", "espero 5% ao mês"):
        lead = servico.processar_mensagem(lead, msg)
    assert "Quer agendar" in lead.historico[-1].conteudo or "?" in lead.historico[-1].conteudo

    lead = servico.processar_mensagem(lead, "não")
    assert "Sem problemas" in lead.historico[-1].conteudo
    assert not lead.agendamentos

    lead = servico.processar_mensagem(lead, "quero comprar um imovel na zona leste, mooca")
    resposta = lead.historico[-1].conteudo
    assert not lead.agendamentos, "não pode agendar sem qualificar a nova busca"
    assert "quarto" in resposta.lower()  # pergunta o que falta
    assert lead.perfil.intencao.value == "compra"
    assert lead.perfil.ticket_investimento is None and lead.perfil.expectativa_retorno is None

    lead = servico.processar_mensagem(lead, "2 quartos")
    assert not lead.agendamentos
    assert "orçamento" in lead.historico[-1].conteudo.lower() or "valor" in lead.historico[-1].conteudo.lower()
    assert lead.perfil.faixa_preco_max is None  # os 500 mil do investimento NÃO viram orçamento

    lead = servico.processar_mensagem(lead, "até 600 mil")
    assert lead.perfil.faixa_preco_max == 600000
    assert not lead.agendamentos


def test_busca_flexivel_avisa_quando_nao_atende_exatamente(tmp_path):
    """Nada exato na região pedida: mostra primeiro o que há NELA com valor
    próximo (sinalizado) — não pula para outra região da cidade."""
    from src.agents.property_agent import ConsultorImoveisAgent

    imoveis = SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json="tests/fixtures/imoveis_teste.json")
    agente = ConsultorImoveisAgent(MockLLMProvider(), imoveis, TfidfVectorSearch(imoveis))
    estado = {
        "intencao": "compra", "regiao_interesse": "Zona Leste, Mooca", "quartos_desejados": 2,
        "faixa_preco_max": 600000, "mensagem_usuario": "até 600 mil", "historico_mensagens": [],
    }
    sugeridos = agente._buscar_imoveis(estado)
    assert [im.bairro for im in sugeridos] == ["Tatuapé"]  # na região pedida, um pouco acima
    lista = agente._montar_lista(estado, sugeridos)
    assert "ACIMA do orçamento" in lista and "OUTRA região" not in lista


def test_busca_exata_por_um_dos_termos_da_regiao(tmp_path):
    from src.agents.property_agent import ConsultorImoveisAgent

    imoveis = SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json="tests/fixtures/imoveis_teste.json")
    agente = ConsultorImoveisAgent(MockLLMProvider(), imoveis, TfidfVectorSearch(imoveis))
    estado = {
        "intencao": "compra", "regiao_interesse": "Mooca, Zona Leste", "quartos_desejados": 3,
        "faixa_preco_max": 800000, "mensagem_usuario": "x", "historico_mensagens": [],
    }
    sugeridos = agente._buscar_imoveis(estado)
    assert [im.bairro for im in sugeridos] == ["Tatuapé"]
    assert "observação" not in agente._montar_lista(estado, sugeridos)
