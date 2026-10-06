"""Testes dos repositórios de corretor e agenda (SQLite), usados pelo
cadastro de corretores e pela consulta de agenda ("Área do Corretor").
Cada teste usa um banco temporário, populado a partir dos seeds reais
(`data/corretores.json`), para não interferir com o banco de
desenvolvimento."""
import tempfile
from pathlib import Path

from src.domain.entities import Agendamento
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository
from src.infrastructure.repositories.sqlite_corretor_repository import (
    SqliteCorretorRepository,
)


def _criar_corretor_repository(tmp_dir: str) -> SqliteCorretorRepository:
    return SqliteCorretorRepository(
        caminho_db=Path(tmp_dir) / "agente_sdr_teste.db",
        caminho_seed_json="data/corretores.json",
    )


def test_popula_corretores_a_partir_do_seed_json():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repositorio = _criar_corretor_repository(tmp_dir)
        corretores = repositorio.listar_todos()
        assert len(corretores) == 9  # 8 por zona + especialista em investimentos


def test_buscar_por_zona_encontra_corretores_da_zona_leste():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repositorio = _criar_corretor_repository(tmp_dir)
        corretores = repositorio.buscar_por_zona("Zona Leste")
        assert len(corretores) >= 1
        assert all("Zona Leste" in c.zonas_atuacao for c in corretores)


def test_buscar_por_zona_sem_correspondencia_retorna_lista_vazia():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repositorio = _criar_corretor_repository(tmp_dir)
        assert repositorio.buscar_por_zona("Zona Inexistente") == []


def test_buscar_por_id_retorna_corretor_correto():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repositorio = _criar_corretor_repository(tmp_dir)
        pedro = repositorio.buscar_por_id("COR001")
        assert pedro is not None
        assert pedro.nome == "Pedro Almeida"
        assert "Zona Leste" in pedro.zonas_atuacao


def test_buscar_por_id_inexistente_retorna_none():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repositorio = _criar_corretor_repository(tmp_dir)
        assert repositorio.buscar_por_id("COR999") is None


def test_registrar_e_listar_agendamento_por_corretor():
    with tempfile.TemporaryDirectory() as tmp_dir:
        caminho_db = Path(tmp_dir) / "agente_sdr_teste.db"
        agenda = SqliteAgendaRepository(caminho_db=caminho_db)

        agendamento = Agendamento(
            lead_id="lead-1", quando_sugerido="amanhã às 10h", corretor_id="COR001"
        )
        agenda.registrar(agendamento)

        agendamentos = agenda.listar_por_corretor("COR001")
        assert len(agendamentos) == 1
        assert agendamentos[0].id == agendamento.id


def test_listar_por_cliente_filtra_pelo_cpf():
    with tempfile.TemporaryDirectory() as tmp_dir:
        caminho_db = Path(tmp_dir) / "agente_sdr_teste.db"
        agenda = SqliteAgendaRepository(caminho_db=caminho_db)

        agenda.registrar(Agendamento(lead_id="lead-1", cliente_cpf="11144477735"))
        agenda.registrar(Agendamento(lead_id="lead-2", cliente_cpf="22233344456"))

        agendamentos = agenda.listar_por_cliente("11144477735")
        assert len(agendamentos) == 1
        assert agendamentos[0].lead_id == "lead-1"


def test_contar_agendamentos_ativos_ignora_cancelados():
    with tempfile.TemporaryDirectory() as tmp_dir:
        caminho_db = Path(tmp_dir) / "agente_sdr_teste.db"
        agenda = SqliteAgendaRepository(caminho_db=caminho_db)

        agenda.registrar(Agendamento(lead_id="lead-1", corretor_id="COR001", status="sugerido"))
        agenda.registrar(Agendamento(lead_id="lead-2", corretor_id="COR001", status="cancelado"))

        assert agenda.contar_agendamentos_ativos("COR001") == 1


def test_registrar_atualiza_status_quando_id_repete():
    with tempfile.TemporaryDirectory() as tmp_dir:
        caminho_db = Path(tmp_dir) / "agente_sdr_teste.db"
        agenda = SqliteAgendaRepository(caminho_db=caminho_db)

        agendamento = Agendamento(lead_id="lead-1", corretor_id="COR001", status="sugerido")
        agenda.registrar(agendamento)

        agendamento.status = "confirmado"
        agenda.registrar(agendamento)

        agendamentos = agenda.listar_por_corretor("COR001")
        assert len(agendamentos) == 1
        assert agendamentos[0].status == "confirmado"
