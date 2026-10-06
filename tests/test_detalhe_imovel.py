"""Regressão (03/10): "quero mais informação do imóvel do Tatuapé" repetia as
sugestões genéricas em vez de detalhar o imóvel pedido."""
from pathlib import Path

import pytest

from src.agents.foco_imovel import pediu_detalhes, resolver_imovel_citado
from src.agents.graph import construir_grafo_sdr
from src.agents.property_detail_agent import DetalheImovelAgent
from src.agents.scheduler_agent import AgendadorAgent
from src.infrastructure.crm.mock_crm import MockCRM
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
from src.infrastructure.rag.vector_store import TfidfVectorSearch
from src.infrastructure.repositories.json_mapa_bairros import carregar_mapa_bairros
from src.infrastructure.repositories.sqlite_corretor_repository import SqliteCorretorRepository
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository
from src.services.conversation_service import ConversationService

MAPA = carregar_mapa_bairros("data/bairros_sp.json")


@pytest.fixture()
def repo(tmp_path):
    return SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json="tests/fixtures/imoveis_teste.json")


@pytest.mark.parametrize(
    "texto",
    ["quero mais informação do imovel do Tatuapé", "me fala mais sobre o garden", "quais os detalhes?",
     "como é esse imóvel?", "tem vaga de garagem?"],
)
def test_detecta_pedido_de_detalhes(texto):
    assert pediu_detalhes(texto)


@pytest.mark.parametrize("texto", ["quero comprar na Mooca", "sim", "até 600 mil"])
def test_nao_e_pedido_de_detalhes(texto):
    assert not pediu_detalhes(texto)


def test_resolve_imovel_por_bairro_titulo_e_referencia(repo):
    imoveis = repo.listar_todos()
    assert resolver_imovel_citado("mais informação do imóvel do Tatuapé", imoveis).id == "IM012"
    assert resolver_imovel_citado("me fala do garden", imoveis).bairro == "Ipiranga"
    assert resolver_imovel_citado("como é esse imóvel?", imoveis, foco_id="IM007").id == "IM007"
    assert resolver_imovel_citado("como é esse imóvel?", imoveis) is None


def test_ficha_com_calculos(repo):
    agente = DetalheImovelAgent(MockLLMProvider(), repo, mapa_bairros=MAPA)
    tatuape = next(im for im in repo.listar_todos() if im.id == "IM012")
    ficha = agente.montar_ficha(tatuape, {"intencao": "compra", "faixa_preco_max": 350000})
    assert "R$ 750.000,00" in ficha and "110 m²" in ficha
    assert "R$ 400.000,00" in ficha and "ACIMA" in ficha  # 750 mil - 350 mil
    assert "Preço por m²: R$ 6.818,18" in ficha and "abaixo" in ficha
    assert "Mooca" in ficha  # bairro vizinho


def _servico(tmp_path):
    imoveis = SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json="tests/fixtures/imoveis_teste.json")
    leads = SqliteLeadRepository(tmp_path / "sdr.db")
    agenda = SqliteAgendaRepository(caminho_db=tmp_path / "sdr.db")
    grafo = construir_grafo_sdr(
        llm_provider=MockLLMProvider(), repositorio_imoveis=imoveis,
        busca_semantica=TfidfVectorSearch(imoveis, MAPA), crm=MockCRM(tmp_path / "crm.json"),
        lead_repository=leads,
        corretor_repository=SqliteCorretorRepository(caminho_db=tmp_path / "sdr.db", caminho_seed_json=Path("data/corretores.json")),
        agenda_repository=agenda, mapa_bairros=MAPA,
    )

    class _Obs:
        def registrar_evento(self, *_):
            pass

    return ConversationService(grafo, leads, MockCRM(tmp_path / "crm.json"), _Obs()), agenda


def test_conversa_detalha_o_imovel_e_agenda_visita_nele(tmp_path):
    servico, agenda = _servico(tmp_path)
    lead = servico.obter_ou_criar_lead(None)
    for msg in ("Sou Fernando Monin, CPF 529.982.247-25", "quero comprar um imóvel na zona leste, mooca",
                "2 quartos", "até 350 mil"):
        lead = servico.processar_mensagem(lead, msg)
    regiao_antes = lead.perfil.regiao_interesse

    lead = servico.processar_mensagem(lead, "quero mais informação do imovel do Tatuapé")
    resposta = lead.historico[-1].conteudo
    assert "Casa em condomínio fechado" in resposta and "IM012" in resposta
    assert "Casa Verde" not in resposta  # não repete as outras sugestões
    assert lead.perfil.imovel_interesse_id == "IM012"
    assert lead.perfil.regiao_interesse == regiao_antes  # pergunta sobre imóvel não muda a região

    lead = servico.processar_mensagem(lead, "quero agendar uma visita")
    ag = agenda.listar_por_cliente("52998224725")[-1]
    assert ag.imovel_id == "IM012"  # visita ao imóvel detalhado
