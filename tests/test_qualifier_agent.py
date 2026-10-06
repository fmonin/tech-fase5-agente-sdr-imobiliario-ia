"""Testes do QualificadorAgent — em especial a reclassificação de valores
monetários entre "faixa de preço" (compra/aluguel) e "ticket de
investimento", que é o campo onde um bug real já apareceu (veja o
docstring de `QualificadorAgent.__call__`)."""
from src.agents.qualifier_agent import QualificadorAgent
from src.infrastructure.llm.mock_provider import MockLLMProvider


def _estado_base(mensagem: str, **overrides) -> dict:
    estado = {"mensagem_usuario": mensagem, "lead_id": "lead-teste"}
    estado.update(overrides)
    return estado


def test_valor_com_intencao_investimento_na_mesma_mensagem_vira_ticket():
    agente = QualificadorAgent(MockLLMProvider())
    resultado = agente(_estado_base("quero investir 500.000"))

    assert resultado["intencao"] == "investimento"
    assert resultado["ticket_investimento"] == 500000.0
    assert resultado.get("faixa_preco_max") is None


def test_valor_sem_repetir_investir_mas_com_intencao_ja_conhecida_vira_ticket():
    # Bug relatado: a intenção "investimento" já foi identificada num turno
    # anterior (carregada em `estado["intencao"]`), mas a mensagem atual tem
    # um erro de digitação ("invetis") e não bate com nenhuma palavra-chave
    # de intenção. Mesmo assim, o valor "500.000" precisa virar
    # ticket_investimento, não faixa_preco_max — senão o lead nunca
    # completa a qualificação e o agente fica "voltando" para perguntas
    # já respondidas.
    agente = QualificadorAgent(MockLLMProvider())
    resultado = agente(_estado_base("quero invetis 500.000", intencao="investimento"))

    assert resultado["intencao"] == "investimento"
    assert resultado["ticket_investimento"] == 500000.0
    assert resultado.get("faixa_preco_max") is None
    assert resultado.get("faixa_preco_min") is None


def test_valor_com_intencao_compra_continua_como_faixa_de_preco():
    agente = QualificadorAgent(MockLLMProvider())
    resultado = agente(_estado_base("quero comprar um apartamento de até 500.000"))

    assert resultado["intencao"] == "compra"
    assert resultado["faixa_preco_max"] == 500000.0
    assert resultado.get("ticket_investimento") is None


def test_qualificacao_completa_apos_ticket_e_retorno_informados():
    agente = QualificadorAgent(MockLLMProvider())
    estado = _estado_base("quero invetis 500.000", intencao="investimento")
    resultado = agente(estado)
    assert resultado["dados_completos"] is False

    estado_com_ticket = {**estado, **resultado, "mensagem_usuario": "espero 1% ao mês de retorno"}
    resultado_final = agente(estado_com_ticket)

    assert resultado_final["dados_completos"] is True
    assert resultado_final["ticket_investimento"] == 500000.0
    assert resultado_final["expectativa_retorno"] == "1% ao mês"
