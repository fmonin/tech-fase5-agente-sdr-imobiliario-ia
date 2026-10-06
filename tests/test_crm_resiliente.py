"""O CRM simulado não pode derrubar o atendimento se o arquivo estiver
corrompido (ex.: processo interrompido no meio da gravação), e grava de forma
atômica (temporário + troca)."""
from src.domain.entities import Lead
from src.infrastructure.crm.mock_crm import MockCRM


def test_crm_corrompido_e_recriado(tmp_path):
    caminho = tmp_path / "crm.json"
    caminho.write_text('{"leads": [ {"id": "x"', encoding="utf-8")  # JSON pela metade
    crm = MockCRM(caminho)
    crm.registrar_lead(Lead(id="L1", canal="web"))
    assert '"L1"' in caminho.read_text(encoding="utf-8")
    assert (tmp_path / "crm.corrompido.json").exists()


def test_gravacao_nao_deixa_temporario(tmp_path):
    caminho = tmp_path / "crm.json"
    MockCRM(caminho).registrar_lead(Lead(id="L2", canal="web"))
    assert not (tmp_path / "crm.tmp").exists()
