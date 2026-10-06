"""Testes das funções de CPF (domínio) — nenhuma delas depende de LLM,
banco ou rede, já que são regras determinísticas (veja o docstring de
`src/domain/cpf.py` sobre por que dado de identidade nunca é "adivinhado"
por um modelo de linguagem neste projeto)."""
from src.domain.cpf import (
    apenas_digitos,
    extrair_cpf_valido,
    formatar_cpf,
    texto_contem_padrao_de_cpf,
    validar_cpf,
)

_CPF_VALIDO = "11144477735"


def test_validar_cpf_aceita_cpf_valido():
    assert validar_cpf(_CPF_VALIDO) is True


def test_validar_cpf_aceita_cpf_valido_formatado():
    assert validar_cpf("111.444.777-35") is True


def test_validar_cpf_rejeita_digito_verificador_errado():
    assert validar_cpf("11144477736") is False


def test_validar_cpf_rejeita_todos_digitos_iguais():
    # 111.111.111-11 "passaria" na fórmula por acidente se não houvesse essa
    # checagem extra — é um erro clássico de digitação, não um CPF de verdade.
    assert validar_cpf("11111111111") is False


def test_validar_cpf_rejeita_tamanho_errado():
    assert validar_cpf("123") is False


def test_apenas_digitos_remove_pontuacao():
    assert apenas_digitos("111.444.777-35") == _CPF_VALIDO


def test_formatar_cpf_adiciona_pontuacao():
    assert formatar_cpf(_CPF_VALIDO) == "111.444.777-35"


def test_extrair_cpf_valido_encontra_cpf_formatado_no_texto():
    texto = "Meu CPF é 111.444.777-35, pode confirmar meu cadastro?"
    assert extrair_cpf_valido(texto) == _CPF_VALIDO


def test_extrair_cpf_valido_encontra_cpf_sem_pontuacao():
    assert extrair_cpf_valido("cpf 11144477735") == _CPF_VALIDO


def test_extrair_cpf_valido_retorna_none_quando_invalido():
    assert extrair_cpf_valido("meu cpf é 111.444.777-99") is None


def test_extrair_cpf_valido_retorna_none_quando_nao_ha_cpf():
    assert extrair_cpf_valido("quero alugar um apartamento de 2 quartos") is None


def test_texto_contem_padrao_de_cpf_mesmo_com_digito_verificador_errado():
    # Diferente de extrair_cpf_valido, aqui só importa "ter a cara" de CPF —
    # é o que permite o IdentificacaoAgent responder "esse CPF parece
    # inválido" em vez de simplesmente ignorar a mensagem.
    assert texto_contem_padrao_de_cpf("111.444.777-99") is True


def test_texto_contem_padrao_de_cpf_falso_quando_nao_ha_11_digitos():
    assert texto_contem_padrao_de_cpf("tenho 2 quartos") is False
