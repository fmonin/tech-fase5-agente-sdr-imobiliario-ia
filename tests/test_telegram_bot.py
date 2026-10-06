"""Bot do Telegram sem rede: sessão chat -> lead (inclusive após recuperar o
cliente pelo CPF) e escolha das fotos a enviar."""
from pathlib import Path
from types import SimpleNamespace

from telegram_bot import SessoesTelegram, fotos_para_enviar, responder
from tests.test_detalhe_imovel import _servico


def test_chat_continua_no_lead_recuperado_pelo_cpf(tmp_path):
    servico, _ = _servico(tmp_path)
    container = SimpleNamespace(conversation_service=servico)
    sessoes = SessoesTelegram(tmp_path / "sessoes.json")

    # Cliente antigo, cadastrado em outro canal
    lead = servico.obter_ou_criar_lead(None)
    for msg in ("Sou Fernando Monin, CPF 529.982.247-25", "quero comprar na zona leste"):
        lead = servico.processar_mensagem(lead, msg)

    responder(container, sessoes, "999", "Olá")
    texto, _ = responder(container, sessoes, "999", "meu CPF é 529.982.247-25")
    assert "Que bom te ver de novo" in texto
    assert sessoes.lead_do_chat("999") == lead.id

    texto, _ = responder(container, sessoes, "999", "2 quartos")
    assert "CPF" not in texto  # não pede identificação de novo

    # Sessão sobrevive a reinício do bot
    assert SessoesTelegram(tmp_path / "sessoes.json").lead_do_chat("999") == lead.id


def test_fotos_para_enviar():
    raiz = Path(__file__).resolve().parents[1]
    imovel = SimpleNamespace(id="IM012", titulo="Casa", fotos=[f"data/imagens/IM012/{n}" for n in
                             ("1_fachada.jpg", "2_sala.jpg", "3_cozinha.jpg")])
    container = SimpleNamespace(repositorio_imoveis=SimpleNamespace(listar_todos=lambda: [imovel]))
    assert len(fotos_para_enviar(container, [("FOTOS", ["IM012"])])) == 3
    capas = fotos_para_enviar(container, [("CAPAS", ["IM012"])])
    assert len(capas) == 1 and capas[0][0] == raiz / "data/imagens/IM012/1_fachada.jpg"
