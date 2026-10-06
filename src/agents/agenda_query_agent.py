"""Agente de Consulta de Agenda (usado na tela "Área do Corretor").

Responsabilidade única: responder, em linguagem natural e VIA LLM, a
perguntas de um corretor sobre a própria agenda ("o que eu tenho essa
semana?", "quantas visitas amanhã?"). Não faz parte do grafo multiagente
do cliente (`src/agents/graph.py`) — é usado diretamente pela tela do
corretor no Streamlit, porque é um fluxo mais simples (pergunta → busca →
resposta), sem qualificação nem roteamento.

Segue o mesmo princípio do `ConsultorImoveisAgent` (RAG): o FILTRO por
período (hoje / amanhã / essa semana / esse mês) é feito em Python, de
forma determinística, a partir da agenda real — o LLM só é usado para
redigir a resposta final a partir dos dados JÁ filtrados, nunca para
decidir sozinho quais agendamentos "contam". Isso evita que o LLM invente
ou esqueça compromissos.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.domain.entities import Agendamento, Corretor
from src.domain.interfaces import IAgendaRepository, ILLMProvider

_log = logging.getLogger(__name__)

_PROMPT_SISTEMA = (
    "Você ajuda um corretor de imóveis a entender a própria agenda. Responda "
    "SOMENTE com base na lista de agendamentos fornecida abaixo — nunca invente "
    "compromissos que não estão na lista. Seja direto e organizado (pode usar uma "
    "lista curta). Se a lista estiver vazia, diga isso claramente."
)


_PROMPT_SAUDACAO = (
    "Você é o assistente pessoal de um corretor de imóveis. Ele ACABOU de "
    "entrar na Área do Corretor. Cumprimente-o pelo primeiro nome de forma "
    "simpática e calorosa (no máximo 1 emoji) e apresente a agenda dele: "
    "primeiro o que tem HOJE e AMANHÃ, depois os próximos compromissos, em uma "
    "lista curta (data, horário, tipo, cliente e imóvel, status). Use SOMENTE "
    "os agendamentos fornecidos — nunca invente compromissos. Se a agenda "
    "estiver vazia, comemore de forma leve que ele está livre. Termine em uma "
    "frase lembrando que ele pode pedir aqui no chat para cancelar ou remarcar "
    "um compromisso (ou usar a lista 'Meus agendamentos'), além de perguntar "
    "qualquer coisa sobre a agenda."
)


class ConsultaAgendaAgent:
    def __init__(self, llm_provider: ILLMProvider, agenda_repository: IAgendaRepository) -> None:
        self._llm = llm_provider
        self._agenda = agenda_repository

    def responder(self, corretor: Corretor, pergunta: str) -> str:
        todos_agendamentos = self._agenda.listar_por_corretor(corretor.id)
        filtrados = self._filtrar_por_periodo(todos_agendamentos, pergunta)

        contexto = self._formatar_contexto(corretor, pergunta, filtrados)
        return self._llm.gerar_resposta(
            [
                {"role": "system", "content": _PROMPT_SISTEMA},
                {"role": "user", "content": contexto},
            ],
            temperatura=0.3,
        )

    def saudar(self, corretor: Corretor) -> str:
        """Mensagem de boas-vindas com a agenda, gerada ao fazer login."""
        agora = datetime.utcnow()
        proximos = sorted(
            (
                a
                for a in self._agenda.listar_por_corretor(corretor.id)
                if a.status != "cancelado"
                and (a.data_hora is None or a.data_hora >= agora - timedelta(hours=1))
            ),
            key=lambda a: a.data_hora or datetime.max,
        )
        hoje = sum(1 for a in proximos if a.data_hora and a.data_hora.date() == agora.date())
        amanha = sum(
            1 for a in proximos if a.data_hora and a.data_hora.date() == (agora + timedelta(days=1)).date()
        )
        contexto = (
            "Saudação de login do corretor.\n"
            f"Hoje é {agora:%d/%m/%Y}. Compromissos hoje: {hoje}; amanhã: {amanha}; "
            f"total de próximos compromissos: {len(proximos)}.\n"
            + self._formatar_contexto(corretor, "(login — apresentar a agenda)", proximos)
        )
        try:
            return self._llm.gerar_resposta(
                [
                    {"role": "system", "content": _PROMPT_SAUDACAO},
                    {"role": "user", "content": contexto},
                ],
                temperatura=0.6,
            )
        except Exception:  # LLM fora do ar: a tela do corretor não pode quebrar por causa da saudação
            _log.warning("LLM indisponível na saudação do corretor; usando a saudação padrão.", exc_info=True)
            primeiro_nome = corretor.nome.split()[0]
            return (f"Olá, {primeiro_nome}! Você tem {hoje} compromisso(s) hoje, {amanha} amanhã "
                    f"e {len(proximos)} no total. Digite **menu** para ver o que posso fazer.")

    @staticmethod
    def _filtrar_por_periodo(agendamentos: list[Agendamento], pergunta: str) -> list[Agendamento]:
        texto = pergunta.lower()
        agora = datetime.utcnow()

        com_data = [a for a in agendamentos if a.data_hora is not None]

        if "hoje" in texto:
            return [a for a in com_data if a.data_hora.date() == agora.date()]
        if "amanhã" in texto or "amanha" in texto:
            amanha = (agora + timedelta(days=1)).date()
            return [a for a in com_data if a.data_hora.date() == amanha]
        if "semana" in texto:
            limite = agora + timedelta(days=7)
            return [a for a in com_data if agora <= a.data_hora <= limite]
        if "mês" in texto or "mes" in texto:
            limite = agora + timedelta(days=30)
            return [a for a in com_data if agora <= a.data_hora <= limite]

        # Sem palavra-chave de período reconhecida: mostra tudo.
        return agendamentos

    @staticmethod
    def _formatar_contexto(corretor: Corretor, pergunta: str, agendamentos: list[Agendamento]) -> str:
        if not agendamentos:
            linhas = "Nenhum agendamento encontrado para esse período."
        else:
            linhas = "\n".join(
                f"- {a.data_hora.strftime('%d/%m/%Y às %Hh%M') if a.data_hora else a.quando_sugerido} "
                f"— {a.tipo} com {a.cliente_nome or 'cliente não identificado'}"
                f"{f' ({a.imovel_titulo})' if a.imovel_titulo else ''} — status: {a.status}"
                for a in agendamentos
            )

        return (
            f"Corretor: {corretor.nome} (área: {', '.join(corretor.zonas_atuacao)})\n"
            f"Pergunta do corretor: {pergunta}\n\n"
            f"Agenda do corretor:\n{linhas}"
        )
