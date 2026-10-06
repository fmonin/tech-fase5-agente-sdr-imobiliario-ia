"""Testes do provedor de LLM mock (garante que a POC funciona sem Azure)."""
from src.infrastructure.llm.mock_provider import MockLLMProvider


def test_extrai_intencao_aluguel():
    provider = MockLLMProvider()
    dados = provider.extrair_dados_estruturados("Quero alugar um apartamento", "")
    assert dados["intencao"] == "aluguel"


def test_extrai_intencao_investimento():
    provider = MockLLMProvider()
    dados = provider.extrair_dados_estruturados("Quero investir em imóveis para ter renda", "")
    assert dados["intencao"] == "investimento"


def test_gerar_resposta_nao_e_vazia():
    provider = MockLLMProvider()
    resposta = provider.gerar_resposta([{"role": "user", "content": "Olá"}])
    assert isinstance(resposta, str) and len(resposta) > 0


def test_esclarecedor_pergunta_o_campo_indicado_mesmo_com_mensagem_sem_palavra_chave():
    # Bug relatado: a última mensagem do lead pode ter um erro de digitação
    # ("invetis" em vez de "investir") e não bater com nenhuma palavra-chave
    # de intenção — o mock não pode "esquecer" o contexto e voltar para a
    # pergunta genérica de boas-vindas. O EsclarecedorAgent já decide o
    # campo de forma determinística e manda pronto em "Próximo campo a
    # perguntar:"; o mock só precisa traduzir isso numa pergunta.
    provider = MockLLMProvider()
    contexto = (
        "Intenção identificada: investimento\n"
        "Já sabemos: região=None, quartos=None, faixa_preco=(None, None), "
        "ticket_investimento=None, expectativa_retorno=None\n"
        "Próximo campo a perguntar: ticket_investimento (quanto o lead pretende investir)\n"
        "Última mensagem do lead: quero invetis 500.000"
    )
    resposta = provider.gerar_resposta([{"role": "user", "content": contexto}])

    assert "buscando comprar, alugar ou investir" not in resposta.lower()
    assert "investir" in resposta.lower()


def test_esclarecedor_pergunta_retorno_quando_so_falta_expectativa_retorno():
    provider = MockLLMProvider()
    contexto = (
        "Intenção identificada: investimento\n"
        "Já sabemos: região=None, quartos=None, faixa_preco=(None, None), "
        "ticket_investimento=500000.0, expectativa_retorno=None\n"
        "Próximo campo a perguntar: expectativa_retorno (qual retorno mensal ele espera)\n"
        "Última mensagem do lead: quero invetis 500.000"
    )
    resposta = provider.gerar_resposta([{"role": "user", "content": contexto}])

    assert "retorno" in resposta.lower()
