"""Regressão da conversa real do Telegram (03/10, 15:59) em que o agente
"se perdeu": pedido de fotos virou agendamento, o corretor mudou ao trocar o
horário, "quero alugar também" reaproveitou dados da compra, a busca vazia
não deu saída, e um "Ok" fez a intenção voltar para compra."""
from tests.test_cenarios_desafio import _conversar, _servico


def test_conversa_nao_se_perde(tmp_path):
    servico = _servico(tmp_path)
    lead, r = _conversar(servico, [
        "Olá", "Sou Fernando Monin, CPF 529.982.247-25",
        "Quero comprar na zona oeste", "3 quartos", "até 990 mil", "60 dias",
    ])
    assert "[[LISTA:" in r[-1] and "[[CAPAS:" not in r[-1]  # lista numerada, fotos só se pedir
    sugerido = r[-1].split("[[LISTA:")[1].split(",")[0].rstrip("]")

    lead = servico.processar_mensagem(lead, "Quero mais fotos")
    assert "De qual imóvel" in lead.historico[-1].conteudo  # vários na lista: pergunta qual
    lead = servico.processar_mensagem(lead, "1")
    assert f"[[FOTOS:{sugerido}]]" in lead.historico[-1].conteudo  # galeria, não agendamento
    assert not lead.agendamentos

    lead = servico.processar_mensagem(lead, "quero agendar uma visita")
    corretor_1 = lead.agendamentos[-1].corretor_nome
    assert lead.agendamentos[-1].imovel_id == sugerido  # visita ao imóvel das fotos
    lead = servico.processar_mensagem(lead, "Quero dia 08.10 as 14 hrs")
    assert "às 14h" in lead.historico[-1].conteudo
    assert lead.agendamentos[-1].corretor_nome == corretor_1  # mesmo corretor

    lead = servico.processar_mensagem(lead, "Quero alugar também")
    p = lead.perfil
    assert p.intencao.value == "aluguel"
    assert (p.quartos_desejados, p.faixa_preco_max, p.urgencia) == (None, None, None)  # nada da compra
    assert any("compra" in b for b in lead.buscas_anteriores)
    assert any(a.status == "sugerido" for a in lead.agendamentos)  # visita da compra segue pendente

    lead = servico.processar_mensagem(lead, "zona leste")
    assert "quartos" in lead.historico[-1].conteudo.lower()  # não pula perguntas
    for msg in ("2 quartos", "até 3 mil", "preciso logo"):
        lead = servico.processar_mensagem(lead, msg)
    assert "Mooca" in lead.historico[-1].conteudo  # aluguel na Mooca (IM018)

    # "Ok" depois da lista: não refaz a busca e a intenção NÃO volta para compra
    lead = servico.processar_mensagem(lead, "Ok")
    assert lead.perfil.intencao.value == "aluguel"
    assert "Como posso te ajudar agora?" in lead.historico[-1].conteudo


def test_ok_sem_convite_nao_refaz_a_busca():
    from src.agents.property_agent import ConsultorImoveisAgent
    from src.infrastructure.llm.mock_provider import MockLLMProvider

    saida = ConsultorImoveisAgent(MockLLMProvider(), None, None)({"mensagem_usuario": "Ol", "historico_mensagens": []})
    assert saida["resposta_agente"].startswith("Certo! Como posso te ajudar agora?")


def test_valores_digitados_de_formas_diferentes():
    from src.agents.qualifier_agent import interpretar_valor_monetario as valor

    assert valor("2500") == 2500 and valor("até 3 mil") == 3000 and valor("R$ 2.500,00") == 2500
    assert valor("1,2 milhão") == 1_200_000 and valor("350k") == 350_000 and valor("até 990 mil") == 990_000
    assert valor("3 quartos") is None


def test_busca_vazia_oferece_ajustes(tmp_path):
    servico = _servico(tmp_path)
    lead, r = _conversar(servico, [
        "Olá", "Sou Ana Souza, CPF 111.444.777-35",
        "Quero alugar na zona central", "4 quartos", "até 1 mil", "preciso logo",
    ])
    assert "o que prefere ajustar" in r[-1] or "observação" in r[-1] or "[[CAPAS:" in r[-1]
