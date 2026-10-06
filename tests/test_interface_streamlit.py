"""Fumaça da interface: as três telas abrem sem erro (uma tela por módulo
em src/interface/paginas/). Usa bancos temporários — nunca o data/ real,
que pode estar em uso pelo Streamlit/bot rodando na máquina."""
import dataclasses
import shutil

from streamlit.testing.v1 import AppTest

import src.container as container_mod


def test_as_tres_telas_abrem_sem_erro(tmp_path, monkeypatch):
    shutil.copy("data/imoveis.json", tmp_path / "imoveis.json")
    monkeypatch.setattr(container_mod, "settings", dataclasses.replace(
        container_mod.settings,
        llm_provider="mock", embeddings_provider="tfidf", telegram_bot_token="", azure_speech_key="",
        database_path=str(tmp_path / "sdr.db"), imoveis_database_path=str(tmp_path / "imoveis.db"),
        imoveis_seed_json_path=str(tmp_path / "imoveis.json"),
        embeddings_cache_path=str(tmp_path / "cache.json"),
    ))
    at = AppTest.from_file("app.py", default_timeout=120).run()
    assert not at.exception
    for tela in ("Painel da Imobiliária", "Área do Corretor"):
        at.sidebar.radio[0].set_value(tela).run()
        assert not at.exception, tela
    assert len(at.tabs) >= 7  # abas da Área do Corretor
