"""Teste de integração leve do grafo multiagente, usando apenas
implementações mock/locais (sem rede, sem Azure) — roda em qualquer
máquina, inclusive em CI.

Usa o `SqlitePropertyRepository` (o mesmo repositório de imóveis usado em
produção, veja `src/container.py`) para que este teste reflita fielmente
como o sistema roda de verdade.
"""
import tempfile
from pathlib import Path

import pytest

from src.agents.graph import construir_grafo_sdr
from src.infrastructure.crm.mock_crm import MockCRM
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
from src.infrastructure.rag.vector_store import TfidfVectorSearch
from src.infrastructure.repositories.sqlite_corretor_repository import SqliteCorretorRepository
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository


@pytest.fixture()
def grafo_montado():
    """Monta um grafo completo (identificação + qualificação + agendamento)
    usando só implementações locais/mock, num diretório temporário."""
    with tempfile.TemporaryDirectory() as tmp:
        repositorio_imoveis = SqlitePropertyRepository(
            caminho_db=Path(tmp) / "imoveis.db", caminho_seed_json="data/imoveis.json"
        )
        lead_repository = SqliteLeadRepository(Path(tmp) / "agente_sdr.db")
        corretor_repository = SqliteCorretorRepository(
            caminho_db=Path(tmp) / "agente_sdr.db", caminho_seed_json="data/corretores.json"
        )
        agenda_repository = SqliteAgendaRepository(caminho_db=Path(tmp) / "agente_sdr.db")

        grafo = construir_grafo_sdr(
            llm_provider=MockLLMProvider(),
            repositorio_imoveis=repositorio_imoveis,
            busca_semantica=TfidfVectorSearch(repositorio_imoveis),
            crm=MockCRM(Path(tmp) / "crm.json"),
            lead_repository=lead_repository,
            corretor_repository=corretor_repository,
            agenda_repository=agenda_repository,
        )
        yield grafo, lead_repository, corretor_repository, agenda_repository


def test_grafo_pede_identificacao_quando_cliente_nao_identificado(grafo_montado):
    grafo, *_ = grafo_montado

    estado_final = grafo.invoke(
        {
            "lead_id": "lead-teste",
            "mensagem_usuario": "Quero alugar um apartamento de 2 quartos na zona sul",
            "temperatura": "frio",
            "cliente_identificado": False,
            "historico_mensagens": [],
        }
    )

    # Sem cadastro concluído, o grafo deve parar no IdentificacaoAgent (nem
    # roda qualificação/imóveis) e pedir nome+CPF ou perguntar se é a
    # primeira vez do cliente.
    assert estado_final.get("resposta_agente")
    assert not estado_final.get("cliente_identificado")
    assert estado_final.get("intencao") in (None, "indefinida")


def test_grafo_responde_a_mensagem_apos_cliente_identificado(grafo_montado):
    grafo, *_ = grafo_montado

    estado_final = grafo.invoke(
        {
            "lead_id": "lead-teste",
            "mensagem_usuario": "Quero alugar um apartamento de 2 quartos na zona sul",
            "temperatura": "frio",
            "cliente_identificado": True,
            "cliente_cpf": "11144477735",
            "cliente_nome": "Maria Teste",
            "historico_mensagens": [],
        }
    )

    assert estado_final.get("resposta_agente")
    assert estado_final["intencao"] in ("aluguel", "compra", "investimento", "indefinida")
