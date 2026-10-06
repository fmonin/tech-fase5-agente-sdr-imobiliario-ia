"""Busca por proximidade (bairro vizinho / mesma zona), orçamento com valor
único e RAG com embeddings (cliente Azure simulado)."""
from types import SimpleNamespace

import pytest

from src.agents.property_agent import ConsultorImoveisAgent
from src.agents.qualifier_agent import QualificadorAgent
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.rag.embedding_search import AzureOpenAIEmbedder, EmbeddingVectorSearch
from src.infrastructure.rag.vector_store import TfidfVectorSearch, representacao_textual
from src.infrastructure.repositories.json_mapa_bairros import carregar_mapa_bairros
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository

MAPA = carregar_mapa_bairros("data/bairros_sp.json")


@pytest.fixture()
def repo(tmp_path):
    return SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json="tests/fixtures/imoveis_teste.json")


def _agente(repo):
    return ConsultorImoveisAgent(MockLLMProvider(), repo, TfidfVectorSearch(repo, MAPA), mapa_bairros=MAPA)


def _estado(**kw):
    base = {"intencao": "compra", "mensagem_usuario": "x", "historico_mensagens": []}
    return {**base, **kw}


def test_mapa_de_bairros():
    assert MAPA.zona_de("mooca") == "Zona Leste"
    assert {"Tatuapé", "Ipiranga"} <= set(MAPA.vizinhos_de("Mooca"))
    assert "Mooca" in MAPA.vizinhos_de("Ipiranga")  # vizinhança simétrica


def test_nada_na_mooca_sugere_bairro_vizinho_dentro_do_orcamento(repo):
    agente = _agente(repo)
    estado = _estado(regiao_interesse="Mooca", quartos_desejados=2, faixa_preco_max=500000)
    sugeridos = agente._buscar_imoveis(estado)
    assert sugeridos[0].bairro == "Ipiranga"  # vizinho da Mooca, R$ 480 mil
    assert "VIZINHO de Mooca" in agente._observacoes[sugeridos[0].id]


def test_caso_relatado_mooca_ate_350_mil(repo):
    """Mooca, 2 quartos, até R$ 350 mil (base antiga, sem imóvel na Mooca):
    mostra o bairro VIZINHO com valor próximo (Ipiranga, 37% acima) em vez de
    pular para outra região da cidade."""
    agente = _agente(repo)
    estado = _estado(regiao_interesse="Zona Leste, Mooca", quartos_desejados=2, faixa_preco_max=350000)
    sugeridos = agente._buscar_imoveis(estado)
    assert [im.bairro for im in sugeridos] == ["Ipiranga"]
    lista = agente._montar_lista(estado, sugeridos)
    assert "VIZINHO de Mooca" in lista and "37% ACIMA do orçamento" in lista
    assert "OUTRA região" not in lista


def test_valor_unico_de_orcamento_e_teto_nao_piso():
    dados = {"faixa_preco_min": 350000.0, "faixa_preco_max": 350000.0}
    QualificadorAgent._normalizar_orcamento(dados)
    assert dados == {"faixa_preco_min": None, "faixa_preco_max": 350000.0}


def test_representacao_inclui_vizinhanca(repo):
    garden = next(im for im in repo.listar_todos() if im.bairro == "Ipiranga")
    assert "Mooca" in representacao_textual(garden, MAPA)


class _EmbedderFalso:
    """Simula um modelo de embeddings (sentence-transformers ou Azure):
    vetor = contagem de algumas palavras-chave."""

    PALAVRAS = ["mooca", "ipiranga", "investimento", "aluguel", "familia", "metro"]

    def __init__(self):
        self.nome = "falso"
        self.chamadas = 0

    def gerar(self, textos):
        self.chamadas += 1
        return [[t.lower().count(p) + 0.01 for p in self.PALAVRAS] for t in textos]


def test_embeddings_buscam_e_usam_cache(repo, tmp_path):
    cache = tmp_path / "emb.json"
    busca = EmbeddingVectorSearch(repo, _EmbedderFalso(), cache, MAPA)
    assert busca.ativo and cache.exists()
    resultado = busca.buscar_similares("algo perto da mooca", top_k=2)
    assert any(im.bairro in ("Ipiranga", "Tatuapé") for im in resultado)

    segundo = _EmbedderFalso()
    EmbeddingVectorSearch(repo, segundo, cache, MAPA)
    assert segundo.chamadas == 0  # imóveis vieram do cache


def test_embeddings_indisponiveis_usam_tfidf(repo, tmp_path):
    class _Quebrado:
        nome = "quebrado"

        def gerar(self, textos):
            raise RuntimeError("modelo não encontrado")

    reserva = TfidfVectorSearch(repo, MAPA)
    busca = EmbeddingVectorSearch(repo, _Quebrado(), tmp_path / "e.json", MAPA, reserva)
    assert not busca.ativo
    assert busca.buscar_similares("studio para investidor", 1)[0].finalidade_investimento


def test_embedder_azure_usa_o_cliente():
    cliente = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=lambda model, input: SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0]) for _ in input])
        )
    )
    assert AzureOpenAIEmbedder(cliente, "emb").gerar(["a", "b"]) == [[1.0, 0.0], [1.0, 0.0]]


def test_factory_sem_biblioteca_cai_no_tfidf(repo, monkeypatch):
    import builtins

    from src.config import settings
    from src.infrastructure.rag import factory

    import_original = builtins.__import__

    def sem_sentence_transformers(nome, *args, **kwargs):
        if nome.startswith("sentence_transformers"):
            raise ImportError("não instalado")
        return import_original(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sem_sentence_transformers)
    from dataclasses import replace

    monkeypatch.setattr(factory, "settings", replace(settings, embeddings_provider="local"))
    assert isinstance(factory.criar_busca_semantica(repo, MAPA), TfidfVectorSearch)


def test_pediu_so_a_zona_sugere_bairro_na_divisa(repo):
    agente = _agente(repo)
    estado = _estado(regiao_interesse="Zona Leste", quartos_desejados=2, faixa_preco_max=500000)
    sugeridos = agente._buscar_imoveis(estado)
    assert sugeridos[0].bairro == "Ipiranga"
    assert "divisa da Zona Leste" in agente._observacoes[sugeridos[0].id]
