"""Agente Recapitulador.

Responsabilidade única: responder quando o lead pede para relembrar a
conversa ("o que conversamos?", "me lembra onde paramos"). Muito comum em
clientes recorrentes, que voltam dias depois e são reconhecidos pelo CPF.

Segue o mesmo princípio do EsclarecedorAgent: o CONTEÚDO do resumo (o que
já sabemos) é montado de forma determinística em Python a partir do perfil
do lead — o LLM só deixa o texto mais natural, e é instruído a não inventar
nada além do rascunho. Assim o resumo nunca "alucina" dados do cliente.
"""
from __future__ import annotations

from src.agents.contexto_conversa import (
    MARCADOR_RASCUNHO_RESUMO,
    descrever_interesse,
    historico_anterior,
)
from src.agents.state import EstadoConversa
from src.config import settings
from src.domain.interfaces import ILLMProvider

_PROMPT_SISTEMA = (
    f"Você é {settings.agente_nome}, um agente imobiliário (SDR) humanizado. "
    "O cliente pediu para relembrar o que já foi conversado. Reescreva o "
    "rascunho abaixo de forma calorosa e breve (2-4 frases), SEM inventar "
    "nenhuma informação que não esteja no rascunho, e termine perguntando "
    "como ele quer continuar."
)


class RecapituladorAgent:
    def __init__(self, llm_provider: ILLMProvider) -> None:
        self._llm = llm_provider

    def __call__(self, estado: EstadoConversa) -> dict:
        rascunho = self.montar_rascunho(estado)
        mensagens = [{"role": "system", "content": _PROMPT_SISTEMA}]
        mensagens.extend(historico_anterior(estado))
        mensagens.append({"role": "user", "content": f"{MARCADOR_RASCUNHO_RESUMO}\n{rascunho}"})
        return {"resposta_agente": self._llm.gerar_resposta(mensagens, temperatura=0.3)}

    @staticmethod
    def montar_rascunho(estado: EstadoConversa) -> str:
        interesse = descrever_interesse(estado)
        agendamentos = estado.get("agendamentos_anteriores") or []

        if not interesse and not agendamentos:
            return (
                "Ainda não tenho detalhes do seu interesse registrados. Você está "
                "buscando comprar, alugar ou investir em um imóvel?"
            )

        linhas = ["Claro! Aqui está o que já conversamos:"]
        if interesse:
            linhas.append(f"- Seu interesse: {interesse}.")
        if agendamentos:
            linhas.append(f"- Agendamento: {agendamentos[-1]}.")
        linhas.append("Quer continuar de onde paramos?")
        return "\n".join(linhas)
