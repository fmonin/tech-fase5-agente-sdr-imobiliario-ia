"""Utilitários para dar "memória" da conversa aos agentes.

Por que este arquivo existe:
    O `ConversationService` já recupera do SQLite o histórico completo do
    lead e coloca em `estado["historico_mensagens"]`. Mas, até a correção
    deste módulo, NENHUM agente lia esse campo — cada agente só enxergava a
    última mensagem. Resultado: ao retomar uma conversa, o histórico
    aparecia na tela, mas a LLM "esquecia" o que o lead já tinha dito (ex.:
    perguntava de novo a região).

    Aqui centralizamos (Single Responsibility Principle) as regras de como
    o histórico e o perfil já coletado são formatados para a LLM, para que
    todos os agentes usem exatamente o mesmo formato.
"""
from __future__ import annotations

import re
from typing import Any

from src.agents.state import EstadoConversa
from src.domain.midia import remover_marcadores

# Fim da pergunta "Quer continuar de onde paramos ou prefere buscar outra coisa?"
# (saudação do cliente recorrente e resposta a um "não"). Um "não" a ESTA
# pergunta significa "quero outra coisa" -> recomeça a busca.
PERGUNTA_CONTINUAR = "ou prefere buscar outra coisa?"

# Pergunta feita depois de mostrar opções no bairro pedido. Um "sim" a ela
# amplia a busca para os bairros vizinhos.
PERGUNTA_VIZINHOS = "Quer que eu te mostre as opções nos bairros vizinhos?"

# Quantas mensagens anteriores enviar para a LLM. Limitamos para controlar
# custo/tokens; o que for mais antigo continua preservado no PERFIL do lead
# (região, quartos, faixa de preço...), que é sempre enviado inteiro.
LIMITE_MENSAGENS_HISTORICO = 12

# Para EXTRAIR dados (Qualificador) usamos uma janela bem maior: mensagens
# curtas do lead custam poucos tokens e isso permite reconstruir o perfil
# de clientes que voltam depois de muito tempo (ex.: a região dita há 20
# mensagens atrás).
LIMITE_MENSAGENS_EXTRACAO = 60

MARCADOR_RASCUNHO_RESUMO = "Rascunho do resumo:"
MARCADOR_ULTIMA_MENSAGEM = "Última mensagem do lead:"
MARCADOR_HISTORICO = "Histórico da conversa:"

# CPF nunca é enviado para a LLM (privacidade/LGPD) e também não pode ser
# confundido com valores monetários pela extração de dados.
_PADRAO_CPF = re.compile(r"\d{3}\.?\d{3}\.?\d{3}[-.]?\d{2}")

_VALORES_VAZIOS = {"", "null", "none", "n/a", "na", "nenhum", "nenhuma", "não informado", "nao informado", "desconhecido"}

CAMPOS_NUMERICOS_FLOAT = {"faixa_preco_min", "faixa_preco_max", "ticket_investimento"}
CAMPOS_NUMERICOS_INT = {"quartos_desejados"}

def historico_anterior(estado: EstadoConversa, limite: int = LIMITE_MENSAGENS_HISTORICO) -> list[dict]:
    """Retorna as últimas `limite` mensagens ANTERIORES à mensagem atual.

    O `ConversationService` registra a mensagem atual no histórico antes de
    rodar o grafo; por isso removemos a última entrada quando ela é a
    própria mensagem que está sendo processada (evita duplicá-la no prompt).
    """
    historico = list(estado.get("historico_mensagens") or [])
    mensagem_atual = estado.get("mensagem_usuario")
    if historico and historico[-1].get("role") == "user" and historico[-1].get("content") == mensagem_atual:
        historico = historico[:-1]
    historico = historico[-limite:] if limite else historico
    return [
        {"role": m.get("role", "user"), "content": mascarar_cpf(remover_marcadores(str(m.get("content", ""))))}
        for m in historico
    ]


def mascarar_cpf(texto: str) -> str:
    return _PADRAO_CPF.sub("[CPF]", texto)


def formatar_historico(estado: EstadoConversa, limite: int = LIMITE_MENSAGENS_HISTORICO) -> str:
    """Histórico em texto corrido: uma linha por mensagem, prefixada com
    "Lead:" ou "Agente:"."""
    linhas = []
    for mensagem in historico_anterior(estado, limite):
        autor = "Lead" if mensagem.get("role") == "user" else "Agente"
        conteudo = " ".join(str(mensagem.get("content", "")).split())
        linhas.append(f"{autor}: {conteudo}")
    return "\n".join(linhas)


def formatar_perfil(estado: EstadoConversa) -> str:
    """Resumo legível do que JÁ sabemos sobre o lead."""
    def mostrar(valor: Any) -> str:
        return "não informado" if valor is None or valor == "" else str(valor)

    return (
        f"- Intenção: {mostrar(estado.get('intencao'))}\n"
        f"- Região de interesse: {mostrar(estado.get('regiao_interesse'))}\n"
        f"- Quartos desejados: {mostrar(estado.get('quartos_desejados'))}\n"
        f"- Faixa de preço: {mostrar(estado.get('faixa_preco_min'))} a {mostrar(estado.get('faixa_preco_max'))}\n"
        f"- Urgência: {mostrar(estado.get('urgencia'))}\n"
        f"- Ticket de investimento: {mostrar(estado.get('ticket_investimento'))}\n"
        f"- Expectativa de retorno: {mostrar(estado.get('expectativa_retorno'))}"
    )


def normalizar_valor(campo: str, valor: Any) -> Any:
    """Converte a saída da LLM para o tipo esperado e trata "vazios".

    LLMs reais às vezes devolvem "" / "null" / "não informado" em vez de
    null, ou números como texto ("3"). Sem esta normalização, um "" vindo
    da LLM apagaria uma região já conhecida do lead.
    """
    if valor is None:
        return None
    if isinstance(valor, str):
        valor = valor.strip()
        if valor.lower() in _VALORES_VAZIOS:
            return None
    try:
        if campo in CAMPOS_NUMERICOS_INT:
            return int(float(str(valor).replace(",", ".")))
        if campo in CAMPOS_NUMERICOS_FLOAT:
            if not isinstance(valor, str):
                return float(valor)
            texto = valor.replace("R$", "").strip()
            try:
                return float(texto)
            except ValueError:  # formato brasileiro: "450.000,00"
                return float(texto.replace(".", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return valor


def _fmt_valor(valor: Any) -> str:
    try:
        return f"R$ {float(valor):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return str(valor)


def descrever_interesse(dados: dict) -> str:
    """Frase curta com o que já sabemos do interesse do lead, ex.:
    "aluguel na Zona Sul, 2 quartos, até R$ 3.000". Vazio se nada é conhecido."""
    intencao = dados.get("intencao")
    partes: list[str] = []
    if intencao and intencao != "indefinida":
        partes.append({"compra": "compra de imóvel", "aluguel": "aluguel de imóvel",
                       "investimento": "investimento em imóvel"}.get(intencao, intencao))
    if dados.get("regiao_interesse"):
        partes.append(f"na região {dados['regiao_interesse']}")
    # Só mostra os campos que fazem sentido para a intenção ATUAL (ex.: um
    # ticket de investimento antigo não aparece se agora o interesse é aluguel).
    if intencao == "investimento":
        if dados.get("ticket_investimento"):
            partes.append(f"valor a investir de {_fmt_valor(dados['ticket_investimento'])}")
        if dados.get("expectativa_retorno"):
            partes.append(f"retorno esperado de {dados['expectativa_retorno']}")
    else:
        if dados.get("quartos_desejados"):
            partes.append(f"{dados['quartos_desejados']} quarto(s)")
        if dados.get("faixa_preco_max"):
            partes.append(f"até {_fmt_valor(dados['faixa_preco_max'])}")
    return ", ".join(partes)
