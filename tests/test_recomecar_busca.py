"""Regressão (04/10, Telegram): cliente recorrente responde "Não" a "Quer
continuar de onde paramos ou prefere buscar outra coisa?" e o agente repetia
a mesma pergunta em loop."""
from tests.test_cenarios_desafio import _conversar, _servico

CPF = "529.982.247-25"


def _cliente_com_busca_de_compra(servico):
    lead, _ = _conversar(servico, [
        "Olá", f"Sou Fernando Monin, CPF {CPF}", "Quero comprar na zona oeste", "3 quartos", "até 1 milhão", "60 dias",
    ])
    return lead


def test_nao_na_retomada_recomeca_a_busca(tmp_path):
    servico = _servico(tmp_path)
    _cliente_com_busca_de_compra(servico)

    # Volta outro dia: identifica-se e recusa continuar a busca anterior
    lead, r = _conversar(servico, ["Olá", f"meu CPF é {CPF}", "Não"])
    assert "prefere buscar outra coisa?" in r[1]
    assert "comprar, alugar ou investir" in r[2]           # pergunta o que ele quer agora
    assert "prefere buscar outra coisa?" not in r[2]       # não repete a pergunta (loop)
    p = lead.perfil
    assert p.intencao.value == "indefinida" and p.regiao_interesse is None and p.quartos_desejados is None
    assert any("compra" in b for b in lead.buscas_anteriores)  # a busca anterior fica guardada

    lead = servico.processar_mensagem(lead, "quero alugar")
    assert lead.perfil.intencao.value == "aluguel"
    assert "região" in lead.historico[-1].conteudo.lower()


def test_sim_na_retomada_continua(tmp_path):
    servico = _servico(tmp_path)
    _cliente_com_busca_de_compra(servico)
    lead, r = _conversar(servico, ["Olá", f"meu CPF é {CPF}", "Sim"])
    assert lead.perfil.intencao.value == "compra" and lead.perfil.quartos_desejados == 3


def test_nao_com_nova_intencao_vai_direto(tmp_path):
    servico = _servico(tmp_path)
    _cliente_com_busca_de_compra(servico)
    lead, _ = _conversar(servico, ["Olá", f"meu CPF é {CPF}", "Não, quero alugar"])
    assert lead.perfil.intencao.value == "aluguel" and lead.perfil.quartos_desejados is None
