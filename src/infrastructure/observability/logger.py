"""Observabilidade: logging estruturado + registro de eventos para o dashboard.

Propósito: criar uma trilha auditável de tudo que o sistema faz.

1. `configurar_logging()` ativa o logging padrão do Python, bem formatado.
2. `EventoStore` grava cada evento importante (mensagem recebida, agente
   executado, agendamento criado, follow-up disparado, etc.) em SQLite.
   O dashboard Streamlit lê direto daqui — sem parsing de logs textuais.

Implementa `IObservador` (structural typing via `Protocol`, ver
`src/domain/interfaces.py`) através do método `registrar_evento`.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

_CRIAR_TABELA = """
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    dados_json TEXT NOT NULL,
    criado_em TEXT NOT NULL
)
"""


def configurar_logging(nivel: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, nivel.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


class EventoStore:
    """Guarda eventos de observabilidade em SQLite para alimentar o dashboard."""

    def __init__(self, caminho_db: str | Path = "data/agente_sdr.db") -> None:
        self._caminho = Path(caminho_db)
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger("agente_sdr.eventos")
        with self._conectar() as conexao:
            conexao.execute(_CRIAR_TABELA)

    def _conectar(self) -> sqlite3.Connection:
        return sqlite3.connect(self._caminho)

    def registrar_evento(self, nome: str, dados: dict) -> None:
        self._logger.info("evento=%s dados=%s", nome, dados)
        with self._conectar() as conexao:
            conexao.execute(
                "INSERT INTO eventos (nome, dados_json, criado_em) VALUES (?, ?, ?)",
                (nome, json.dumps(dados, ensure_ascii=False, default=str), datetime.utcnow().isoformat()),
            )

    def listar_eventos(self, limite: int = 200) -> list[dict]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT nome, dados_json, criado_em FROM eventos ORDER BY id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [
            {"nome": nome, "dados": json.loads(dados_json), "criado_em": criado_em}
            for nome, dados_json, criado_em in linhas
        ]
