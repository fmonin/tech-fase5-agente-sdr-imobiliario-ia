"""Depois de remarcar pelo chat, um "sim" do cliente não pode criar um
segundo agendamento no mesmo horário (bug visto ao gerar as capturas)."""
import dataclasses

import src.container as container_mod


def _container(tmp_path, monkeypatch):
    import shutil
    shutil.copy("data/imoveis.json", tmp_path / "imoveis.json")
    monkeypatch.setattr(container_mod, "settings", dataclasses.replace(
        container_mod.settings, llm_provider="mock", embeddings_provider="tfidf", telegram_bot_token="",
        azure_speech_key="", database_path=str(tmp_path / "sdr.db"),
        imoveis_database_path=str(tmp_path / "imoveis.db"), imoveis_seed_json_path=str(tmp_path / "imoveis.json"),
        embeddings_cache_path=str(tmp_path / "cache.json")))
    return container_mod.montar_container()


def test_sim_depois_de_remarcar_nao_duplica(tmp_path, monkeypatch):
    c = _container(tmp_path, monkeypatch)
    cs = c.conversation_service
    lead = cs.obter_ou_criar_lead(None, "web")
    for m in ["Olá", "529.982.247-25", "Mariana Alves",
              "Quero comprar um apartamento de 2 quartos na Mooca até 600 mil", "em 2 meses",
              "quero agendar uma visita sexta às 10h", "sim, pode confirmar",
              "quero remarcar a visita para sábado às 10h"]:
        lead = cs.processar_mensagem(lead, m)
    lead = cs.processar_mensagem(lead, "sim")
    ativos = [a for a in c.agenda_repository.listar_por_cliente("52998224725") if a.status != "cancelado"]
    assert len(ativos) == 1
    assert "já está marcada" in lead.historico[-1].conteudo
