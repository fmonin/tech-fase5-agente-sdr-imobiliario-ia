"""Testes do repositório de imóveis em SQLite (repositório usado por padrão
pela aplicação). Usa um banco temporário, populado a partir do
data/imoveis.json real, para não interferir com o banco de desenvolvimento.
"""
import tempfile
from pathlib import Path

from src.infrastructure.repositories.sqlite_property_repository import (
    SqlitePropertyRepository,
)


def _criar_repositorio(tmp_dir: str) -> SqlitePropertyRepository:
    return SqlitePropertyRepository(
        caminho_db=Path(tmp_dir) / "imoveis_teste.db",
        caminho_seed_json="data/imoveis.json",
    )


def test_popula_o_banco_a_partir_do_seed_json():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repositorio = _criar_repositorio(tmp_dir)
        assert len(repositorio.listar_todos()) > 0


def test_buscar_filtra_por_tipo_negocio_via_sql():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repositorio = _criar_repositorio(tmp_dir)
        alugueis = repositorio.buscar(tipo_negocio="aluguel")
        assert len(alugueis) > 0
        assert all(im.tipo_negocio == "aluguel" for im in alugueis)


def test_buscar_filtra_por_preco_e_quartos_via_sql():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repositorio = _criar_repositorio(tmp_dir)
        resultado = repositorio.buscar(preco_max=400000, quartos_min=1)
        assert all(im.preco <= 400000 and im.quartos >= 1 for im in resultado)


def test_segunda_instancia_nao_duplica_dados():
    """Reabrir o mesmo banco não deve inserir os imóveis de novo (INSERT OR IGNORE)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        caminho_db = Path(tmp_dir) / "imoveis_teste.db"
        repositorio1 = SqlitePropertyRepository(caminho_db, "data/imoveis.json")
        total_inicial = len(repositorio1.listar_todos())

        repositorio2 = SqlitePropertyRepository(caminho_db, "data/imoveis.json")
        assert len(repositorio2.listar_todos()) == total_inicial
