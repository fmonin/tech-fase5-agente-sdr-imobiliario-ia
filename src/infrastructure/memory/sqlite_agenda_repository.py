"""Base de agenda — consultável pelo cliente e pelo corretor, em SQL.

Implementa `IAgendaRepository`. É a resposta ao requisito "temos que ter
uma base para guardar o agendamento e que o cliente ou o corretor possam
consultar": diferente do agendamento "preso" dentro do histórico de um
lead (que só serve pra exibir no chat daquele lead específico), esta
tabela (`agendamentos`, em `data/agente_sdr.db`) é uma base própria,
indexável por CPF do cliente OU por id do corretor — é nela que o
`ConsultaAgendaAgent` (usado na tela "Área do Corretor") faz as buscas.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.infrastructure.sqlite_conexao import conectar
from src.domain.entities import Agendamento
from src.domain.interfaces import IAgendaRepository

_CRIAR_TABELA = """
CREATE TABLE IF NOT EXISTS agendamentos (
    id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL,
    quando_sugerido TEXT NOT NULL,
    tipo TEXT NOT NULL,
    status TEXT NOT NULL,
    data_hora TEXT,
    cliente_cpf TEXT,
    cliente_nome TEXT,
    corretor_id TEXT,
    corretor_nome TEXT,
    imovel_id TEXT,
    imovel_titulo TEXT,
    criado_em TEXT NOT NULL
)
"""

# Colunas adicionadas depois (cancelamento pelo corretor). Bancos criados
# antes ganham essas colunas automaticamente na inicialização (migração
# simples, sem perder os agendamentos já existentes).
_COLUNAS_NOVAS = {
    "cancelado_por": "TEXT",
    "motivo_cancelamento": "TEXT",
    "cancelado_em": "TEXT",
    "cliente_notificado": "INTEGER NOT NULL DEFAULT 0",
    "horario_anterior": "TEXT",
    "motivo_alteracao": "TEXT",
    "corretor_notificado": "INTEGER NOT NULL DEFAULT 1",
    "resultado_visita": "TEXT",
    "motivo_resultado": "TEXT",
    "observacao_resultado": "TEXT",
    "resultado_em": "TEXT",
    "pos_visita_enviado": "INTEGER NOT NULL DEFAULT 0",
    "feedback_cliente": "TEXT",
    "valor_negociado": "REAL",
    "detalhes": "TEXT",
}


class SqliteAgendaRepository(IAgendaRepository):
    def __init__(self, caminho_db: str | Path = "data/agente_sdr.db") -> None:
        self._caminho_db = Path(caminho_db)
        self._caminho_db.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as conexao:
            conexao.execute(_CRIAR_TABELA)
            existentes = {linha["name"] for linha in conexao.execute("PRAGMA table_info(agendamentos)")}
            for coluna, tipo in _COLUNAS_NOVAS.items():
                if coluna not in existentes:
                    conexao.execute(f"ALTER TABLE agendamentos ADD COLUMN {coluna} {tipo}")

    def _conectar(self):
        return conectar(self._caminho_db, linhas_como_dict=True)

    def registrar(self, agendamento: Agendamento) -> None:
        with self._conectar() as conexao:
            conexao.execute(
                """
                INSERT INTO agendamentos (
                    id, lead_id, quando_sugerido, tipo, status, data_hora,
                    cliente_cpf, cliente_nome, corretor_id, corretor_nome,
                    imovel_id, imovel_titulo, criado_em,
                    cancelado_por, motivo_cancelamento, cancelado_em, cliente_notificado,
                    horario_anterior, motivo_alteracao, corretor_notificado,
                    resultado_visita, motivo_resultado, observacao_resultado, resultado_em,
                    pos_visita_enviado, feedback_cliente, valor_negociado, detalhes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    quando_sugerido = excluded.quando_sugerido,
                    horario_anterior = excluded.horario_anterior,
                    motivo_alteracao = excluded.motivo_alteracao,
                    status = excluded.status,
                    data_hora = excluded.data_hora,
                    cancelado_por = excluded.cancelado_por,
                    motivo_cancelamento = excluded.motivo_cancelamento,
                    cancelado_em = excluded.cancelado_em,
                    cliente_notificado = excluded.cliente_notificado,
                    corretor_notificado = excluded.corretor_notificado,
                    resultado_visita = excluded.resultado_visita,
                    motivo_resultado = excluded.motivo_resultado,
                    observacao_resultado = excluded.observacao_resultado,
                    resultado_em = excluded.resultado_em,
                    pos_visita_enviado = excluded.pos_visita_enviado,
                    feedback_cliente = excluded.feedback_cliente,
                    valor_negociado = excluded.valor_negociado,
                    detalhes = excluded.detalhes
                """,
                (
                    agendamento.id,
                    agendamento.lead_id,
                    agendamento.quando_sugerido,
                    agendamento.tipo,
                    agendamento.status,
                    agendamento.data_hora.isoformat() if agendamento.data_hora else None,
                    agendamento.cliente_cpf,
                    agendamento.cliente_nome,
                    agendamento.corretor_id,
                    agendamento.corretor_nome,
                    agendamento.imovel_id,
                    agendamento.imovel_titulo,
                    agendamento.criado_em.isoformat(),
                    agendamento.cancelado_por,
                    agendamento.motivo_cancelamento,
                    agendamento.cancelado_em.isoformat() if agendamento.cancelado_em else None,
                    1 if agendamento.cliente_notificado else 0,
                    agendamento.horario_anterior,
                    agendamento.motivo_alteracao,
                    1 if agendamento.corretor_notificado else 0,
                    agendamento.resultado_visita,
                    agendamento.motivo_resultado,
                    agendamento.observacao_resultado,
                    agendamento.resultado_em.isoformat() if agendamento.resultado_em else None,
                    1 if agendamento.pos_visita_enviado else 0,
                    agendamento.feedback_cliente,
                    agendamento.valor_negociado,
                    agendamento.detalhes,
                ),
            )

    @staticmethod
    def _linha_para_agendamento(linha: sqlite3.Row) -> Agendamento:
        return Agendamento(
            id=linha["id"],
            lead_id=linha["lead_id"],
            quando_sugerido=linha["quando_sugerido"],
            tipo=linha["tipo"],
            status=linha["status"],
            confirmado=(linha["status"] == "confirmado"),
            data_hora=datetime.fromisoformat(linha["data_hora"]) if linha["data_hora"] else None,
            cliente_cpf=linha["cliente_cpf"],
            cliente_nome=linha["cliente_nome"],
            corretor_id=linha["corretor_id"],
            corretor_nome=linha["corretor_nome"],
            imovel_id=linha["imovel_id"],
            imovel_titulo=linha["imovel_titulo"],
            criado_em=datetime.fromisoformat(linha["criado_em"]),
            cancelado_por=linha["cancelado_por"],
            motivo_cancelamento=linha["motivo_cancelamento"],
            cancelado_em=datetime.fromisoformat(linha["cancelado_em"]) if linha["cancelado_em"] else None,
            cliente_notificado=bool(linha["cliente_notificado"]),
            horario_anterior=linha["horario_anterior"],
            motivo_alteracao=linha["motivo_alteracao"],
            corretor_notificado=bool(linha["corretor_notificado"]),
            resultado_visita=linha["resultado_visita"],
            motivo_resultado=linha["motivo_resultado"],
            observacao_resultado=linha["observacao_resultado"],
            resultado_em=datetime.fromisoformat(linha["resultado_em"]) if linha["resultado_em"] else None,
            pos_visita_enviado=bool(linha["pos_visita_enviado"]),
            feedback_cliente=linha["feedback_cliente"],
            valor_negociado=linha["valor_negociado"],
            detalhes=linha["detalhes"],
        )

    def buscar_por_id(self, agendamento_id: str) -> Optional[Agendamento]:
        with self._conectar() as conexao:
            linha = conexao.execute(
                "SELECT * FROM agendamentos WHERE id = ?", (agendamento_id,)
            ).fetchone()
        return self._linha_para_agendamento(linha) if linha else None

    def listar_avisos_corretor(self, corretor_id: str) -> list[Agendamento]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT * FROM agendamentos WHERE corretor_id = ? AND corretor_notificado = 0 ORDER BY data_hora",
                (corretor_id,),
            ).fetchall()
        return [self._linha_para_agendamento(linha) for linha in linhas]

    def listar_avisos_pendentes(self, cpf: str) -> list[Agendamento]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT * FROM agendamentos WHERE cliente_cpf = ? AND cliente_notificado = 0 AND ("
                "(status = 'cancelado' AND cancelado_por = 'corretor') OR "
                "(status != 'cancelado' AND horario_anterior IS NOT NULL)) ORDER BY data_hora",
                (cpf,),
            ).fetchall()
        return [self._linha_para_agendamento(linha) for linha in linhas]

    def listar_por_cliente(self, cpf: str) -> list[Agendamento]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT * FROM agendamentos WHERE cliente_cpf = ? ORDER BY data_hora",
                (cpf,),
            ).fetchall()
        return [self._linha_para_agendamento(linha) for linha in linhas]

    def listar_por_corretor(self, corretor_id: str) -> list[Agendamento]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT * FROM agendamentos WHERE corretor_id = ? ORDER BY data_hora",
                (corretor_id,),
            ).fetchall()
        return [self._linha_para_agendamento(linha) for linha in linhas]

    def contar_agendamentos_ativos(self, corretor_id: str) -> int:
        with self._conectar() as conexao:
            total = conexao.execute(
                "SELECT COUNT(*) FROM agendamentos WHERE corretor_id = ? AND status != 'cancelado'",
                (corretor_id,),
            ).fetchone()[0]
        return total
