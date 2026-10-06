"""Configuração: settings.toml (Git) + settings.local.toml (máquina) + .env (só segredos)."""
import pytest

from src.config import Settings, carregar_settings, fontes_padrao
from src.infrastructure.configuracao import AmbienteFonte, ArquivoTomlFonte


def _escrever(pasta, nome, texto):
    (pasta / "config").mkdir(exist_ok=True)
    (pasta / "config" / nome).write_text(texto, encoding="utf-8")


def test_precedencia_toml_local_e_ambiente(tmp_path):
    _escrever(tmp_path, "settings.toml", '[llm]\nprovider = "azure"\n[followup]\nminutos_sem_resposta = 60\n'
                                         'intervalo_verificacao_minutos = 5\n[agente]\nnome = "Sr. Agim"\n')
    _escrever(tmp_path, "settings.local.toml", "[followup]\nminutos_sem_resposta = 2\n")
    s = carregar_settings(fontes_padrao(tmp_path, ambiente={"FOLLOWUP_INTERVALO_MINUTOS": "1",
                                                             "AZURE_OPENAI_API_KEY": "chave"}))
    assert s.llm_provider == "azure"  # do settings.toml
    assert s.followup_minutos == 2  # settings.local.toml vence o settings.toml
    assert s.followup_intervalo_minutos == 1  # variável de ambiente vence tudo (convertida p/ int)
    assert s.azure_openai_api_key == "chave" and "chave" not in repr(s)  # segredo não aparece em logs
    assert s.pos_visita_horas == 2.0  # padrão do código quando ninguém define


def test_segredo_em_arquivo_versionado_e_recusado(tmp_path):
    _escrever(tmp_path, "settings.toml", '[azure_openai]\napi_key = "vazou"\n')
    with pytest.raises(ValueError, match="SEGREDO"):
        carregar_settings(fontes_padrao(tmp_path, ambiente={}))


def test_provider_auto_usa_azure_so_com_chave():
    sem_chave = Settings(llm_provider="auto", azure_openai_endpoint="https://x.openai.azure.com/")
    com_chave = Settings(llm_provider="AUTO", azure_openai_endpoint="https://x.openai.azure.com/",
                         azure_openai_api_key="k")
    assert not sem_chave.usando_llm_real and com_chave.usando_llm_real
    assert not Settings(llm_provider="mock", azure_openai_api_key="k").usando_llm_real


def test_arquivos_do_projeto_sem_segredos():
    """O settings.toml real do projeto carrega e não contém segredos."""
    from src.config import RAIZ_PROJETO

    for nome in ("settings.toml", "settings.local.toml.example"):
        dados = ArquivoTomlFonte(RAIZ_PROJETO / "config" / nome).ler()
        assert not any(k.endswith(("api_key", "bot_token")) for k in dados)
    carregar_settings([ArquivoTomlFonte(RAIZ_PROJETO / "config" / "settings.toml"), AmbienteFonte({}, {})])


def test_descricao_do_llm_ativo():
    azure = Settings(llm_provider="auto", azure_openai_api_key="k",
                     azure_openai_endpoint="https://meu-recurso.openai.azure.com/", azure_openai_deployment="gpt-4.1-mini")
    assert azure.descricao_llm == "Azure OpenAI — modelo gpt-4.1-mini (recurso meu-recurso)"
    assert "AZURE_OPENAI_API_KEY" in Settings(llm_provider="auto").descricao_llm
    assert 'llm.provider = "mock"' in Settings(llm_provider="mock", azure_openai_api_key="k").descricao_llm
