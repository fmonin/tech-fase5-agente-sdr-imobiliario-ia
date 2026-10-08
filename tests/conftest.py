"""Configuração comum dos testes."""
import pytest

from src.infrastructure.crm.mock_crm import MockCRM


@pytest.fixture(autouse=True)
def _crm_simulado_temporario(tmp_path, monkeypatch):
    """Quem cria `MockCRM()` sem caminho grava numa pasta temporária do teste,
    nunca no data/crm_simulado.json real (que pode estar aberto pelo app ou
    travado pela sincronização do OneDrive)."""
    monkeypatch.setattr(MockCRM.__init__, "__defaults__", (tmp_path / "crm_simulado.json",))
