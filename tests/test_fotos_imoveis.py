"""Fotos dos imóveis: base com >= 3 imagens por imóvel, marcadores de mídia
nas respostas, sincronização JSON -> SQLite e escolha do imóvel em empate."""
import json
import sqlite3
from pathlib import Path

import pytest

from src.agents.foco_imovel import ids_mostrados, resolver_imovel_citado
from src.agents.property_agent import ConsultorImoveisAgent
from src.agents.property_detail_agent import DetalheImovelAgent
from src.domain.midia import anexar_capas, anexar_fotos, nome_ambiente, remover_marcadores, separar_midia
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.rag.vector_store import TfidfVectorSearch
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture()
def repo(tmp_path):
    return SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json=RAIZ / "data" / "imoveis.json")


def test_base_real_tem_pelo_menos_3_fotos_existentes_por_imovel(repo):
    imoveis = repo.listar_todos()
    assert len(imoveis) >= 30
    for im in imoveis:
        assert len(im.fotos) >= 3, im.id
        assert all((RAIZ / f).exists() for f in im.fotos), im.id


def test_marcadores_de_midia():
    texto = anexar_capas(anexar_fotos("Olá", "IM012"), ["IM007", "IM014"])
    limpo, midias = separar_midia(texto)
    assert limpo == "Olá"
    assert midias == [("FOTOS", ["IM012"]), ("CAPAS", ["IM007", "IM014"])]
    assert remover_marcadores(texto) == "Olá"
    assert ids_mostrados(texto) == ["IM012", "IM007", "IM014"]
    assert nome_ambiente("data/imagens/IM012/5_area_lazer.jpg") == "Área de lazer"


def test_detalhe_anexa_galeria_e_consultor_lista_sem_fotos(repo):
    detalhe = DetalheImovelAgent(MockLLMProvider(), repo)
    saida = detalhe({"mensagem_usuario": "quero ver mais fotos do IM012", "historico_mensagens": []})
    assert saida["resposta_agente"].endswith("[[FOTOS:IM012]]")

    consultor = ConsultorImoveisAgent(MockLLMProvider(), repo, TfidfVectorSearch(repo))
    saida = consultor({"intencao": "compra", "regiao_interesse": "Mooca", "quartos_desejados": 2,
                       "faixa_preco_max": 400000, "mensagem_usuario": "x", "historico_mensagens": []})
    assert "[[LISTA:IM016" in saida["resposta_agente"] and "[[CAPAS:" not in saida["resposta_agente"]


def test_empate_prefere_o_imovel_mostrado_ou_pergunta_qual(repo):
    imoveis = repo.listar_todos()  # há dois imóveis no Tatuapé (IM012 e IM019)
    assert resolver_imovel_citado("mais informação do imóvel do Tatuapé", imoveis) is None
    assert resolver_imovel_citado("mais informação do imóvel do Tatuapé", imoveis, mostrados=["IM012"]).id == "IM012"
    assert resolver_imovel_citado("me fala da casa do Tatuapé", imoveis).id == "IM012"

    detalhe = DetalheImovelAgent(MockLLMProvider(), repo)
    saida = detalhe({"mensagem_usuario": "mais informação do imóvel do Tatuapé", "historico_mensagens": []})
    assert "De qual você quer saber mais?" in saida["resposta_agente"]


def test_historico_para_o_llm_nao_tem_marcadores():
    from src.agents.contexto_conversa import historico_anterior

    estado = {"mensagem_usuario": "x", "historico_mensagens": [
        {"role": "assistant", "content": anexar_fotos("Veja as fotos", "IM012")}]}
    assert historico_anterior(estado)[0]["content"] == "Veja as fotos"


def test_banco_antigo_e_sincronizado_com_o_json(tmp_path):
    db = tmp_path / "antigo.db"
    with sqlite3.connect(db) as conexao:  # formato antigo: sem coluna fotos e com dado desatualizado
        conexao.execute(
            "CREATE TABLE imoveis (id TEXT PRIMARY KEY, titulo TEXT NOT NULL, tipo_negocio TEXT NOT NULL,"
            " finalidade_investimento INTEGER NOT NULL, zona TEXT NOT NULL, bairro TEXT NOT NULL, preco REAL NOT NULL,"
            " quartos INTEGER NOT NULL, metragem REAL NOT NULL, descricao TEXT NOT NULL)"
        )
        conexao.execute("INSERT INTO imoveis VALUES ('IM012','velho','venda',0,'Zona Leste','Tatuapé',1,1,1,'x')")
    repo = SqlitePropertyRepository(caminho_db=db, caminho_seed_json=RAIZ / "data" / "imoveis.json")
    im012 = next(im for im in repo.listar_todos() if im.id == "IM012")
    assert im012.titulo == "Casa em condomínio fechado" and len(im012.fotos) >= 3
    assert len(repo.listar_todos()) == len(json.loads((RAIZ / "data" / "imoveis.json").read_text(encoding="utf-8")))
