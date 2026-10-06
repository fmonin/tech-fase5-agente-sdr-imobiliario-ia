"""Agenda do cliente: lista os agendamentos ATIVOS (não cancelados e ainda
por acontecer) de um cliente, cada um com o(a) corretor(a) responsável e o
contato dele(a).

Usado quando o cliente "loga" — se identifica pelo CPF no chat (Streamlit
ou Telegram) ou volta ao bot do Telegram com /start. Montado em Python a
partir da base de agenda (`IAgendaRepository`) — o LLM não participa, então
nenhum horário ou corretor é inventado.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.domain.entities import Agendamento
from src.domain.interfaces import IAgendaRepository, ICorretorRepository


def agendamentos_ativos_do_cliente(agenda: IAgendaRepository | None, cpf: str | None) -> list[Agendamento]:
    if agenda is None or not cpf:
        return []
    agora_brasilia = datetime.utcnow() - timedelta(hours=3)  # horários da agenda estão no horário local
    ativos = [
        a for a in agenda.listar_por_cliente(cpf)
        if a.status != "cancelado" and (a.data_hora is None or a.data_hora >= agora_brasilia)
    ]
    return sorted(ativos, key=lambda a: a.data_hora or datetime.max)


def descrever_agendas_do_cliente(
    agenda: IAgendaRepository | None,
    corretores: ICorretorRepository | None,
    cpf: str | None,
) -> str | None:
    ativos = agendamentos_ativos_do_cliente(agenda, cpf)
    if not ativos:
        return None
    linhas = [_descrever(a, corretores) for a in ativos]
    titulo = (
        "📅 Você tem este agendamento comigo:" if len(linhas) == 1
        else f"📅 Você tem {len(linhas)} agendamentos comigo:"
    )
    return titulo + "\n" + "\n".join(f"{i}) {linha}" for i, linha in enumerate(linhas, start=1))


def _descrever(a: Agendamento, corretores: ICorretorRepository | None) -> str:
    tipo = a.tipo_texto.capitalize()
    imovel = f" — {a.imovel_titulo}" if a.imovel_titulo else ""
    corretor = corretores.buscar_por_id(a.corretor_id) if corretores and a.corretor_id else None
    nome = (corretor.nome if corretor else None) or a.corretor_nome or "a definir"
    contato = f" · {corretor.telefone}" if corretor and corretor.telefone else ""
    situacao = "✅ confirmada" if a.status == "confirmado" else "⏳ aguardando sua confirmação"
    return f"{tipo} {a.quando_formatado()}{imovel}\n   Corretor(a): {nome}{contato} · {situacao}"
