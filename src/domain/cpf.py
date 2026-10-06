"""Utilitários de CPF (documento usado para identificar o cliente).

Por que isso vive no domínio (`src/domain/`) e não em algum agente:
    Validar/extrair um CPF é uma regra de NEGÓCIO (o que é um CPF válido não
    depende de LangGraph, Streamlit ou Azure), então pertence ao domínio —
    igual às entidades e interfaces. Qualquer agente ou serviço que precise
    disso importa daqui, em vez de reescrever a lógica.

Importante (uma decisão de design deste projeto):
    Diferente dos outros agentes (que usam `ILLMProvider` para interpretar
    a mensagem do lead), o `IdentificacaoAgent` NÃO usa o LLM para
    reconhecer CPF ou nome. Dado de identidade (documento, nome) nunca
    deveria ser "adivinhado" por um modelo de linguagem — por isso usamos
    aqui uma extração determinística (regex + validação do dígito
    verificador), que funciona igual com o provedor mock ou com o Azure
    OpenAI.
"""
from __future__ import annotations

import re
from typing import Optional

_REGEX_CPF = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")


def apenas_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)


def formatar_cpf(cpf: str) -> str:
    digitos = apenas_digitos(cpf)
    if len(digitos) != 11:
        return cpf
    return f"{digitos[0:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:11]}"


def validar_cpf(cpf: str) -> bool:
    """Valida o CPF pelo algoritmo oficial do dígito verificador.

    Isso evita cadastrar um cliente com um CPF digitado errado (ex.: um
    número trocado), sem precisar de nenhuma chamada externa.
    """
    digitos = apenas_digitos(cpf)

    if len(digitos) != 11 or digitos == digitos[0] * 11:
        return False

    def _digito_verificador(parte: str) -> int:
        soma = sum(int(d) * peso for d, peso in zip(parte, range(len(parte) + 1, 1, -1)))
        resto = (soma * 10) % 11
        return 0 if resto == 10 else resto

    primeiro_digito = _digito_verificador(digitos[:9])
    segundo_digito = _digito_verificador(digitos[:9] + str(primeiro_digito))

    return digitos[-2:] == f"{primeiro_digito}{segundo_digito}"


def extrair_cpf_valido(texto: str) -> Optional[str]:
    """Procura um CPF no texto e retorna só os dígitos, se for válido.

    Retorna `None` quando não encontra nenhum CPF ou quando o que foi
    encontrado não passa na validação do dígito verificador (provável erro
    de digitação) — nesse caso, o `IdentificacaoAgent` pede para o cliente
    conferir e reenviar.
    """
    match = _REGEX_CPF.search(texto)
    if not match:
        return None

    candidato = apenas_digitos(match.group(0))
    return candidato if validar_cpf(candidato) else None


def texto_contem_padrao_de_cpf(texto: str) -> bool:
    """True se o texto tem "a cara" de um CPF (11 dígitos, formatado ou não),
    mesmo que o dígito verificador não bata — útil para dar um retorno
    diferente ("esse CPF parece inválido") de "não recebi nenhum CPF"."""
    return bool(_REGEX_CPF.search(texto))
