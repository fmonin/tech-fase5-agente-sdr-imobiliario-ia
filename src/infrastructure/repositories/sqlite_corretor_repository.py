"""Repositório de corretores (cadastro), em SQL (SQLite).

Implementa `ICorretorRepository`. Segue o mesmo padrão do
`SqlitePropertyRepository`: um arquivo JSON (`data/corretores.json`) serve
como dado-fonte editável, e na primeira execução os dados são carregados
numa tabela SQL de verdade (`corretores`), guardada no mesmo banco usado
para leads/eventos (`data/agente_sdr.db`) — já que corretores, leads e
agendamentos são todos "dados operacionais" do dia a dia da imobiliária.

`zonas_atuacao` é guardado como uma string separada por vírgulas (ex.:
"Zona Sul,Zona Oeste") porque SQLite não tem um tipo nativo de lista —
uma limitação comum e uma boa oportunidade para explicar esse detalhe a
quem está aprendendo banco de dados relacional.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from src.domain.entities import Corretor
from src.domain.interfaces import ICorretorRepository

_CRIAR_TABELA = """
CREATE TABLE IF NOT EXISTS corretores (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    zonas_atuacao TEXT NOT NULL,
    email TEXT,
    telefone TEXT
)
"""


class SqliteCorretorRepository(ICorretorRepository):
    def __init__(
        self,
        caminho_db: str | Path = "data/agente_sdr.db",
        caminho_seed_json: str | Path = "data/corretores.json",
    ) -> None:
        self._caminho_db = Path(caminho_db)
        self._caminho_db.parent.mkdir(parents=True, exist_ok=True)
        self._caminho_seed_json = Path(caminho_seed_json)

        with self._conectar() as conexao:
            conexao.execute(_CRIAR_TABELA)
        # Sempre aplica o seed com INSERT OR IGNORE: corretores novos do JSON
        # (ex.: o especialista em investimentos) entram sem apagar o banco.
        self._popular_a_partir_do_seed()

    def _conectar(self) -> sqlite3.Connection:
        conexao = sqlite3.connect(self._caminho_db)
        conexao.row_factory = sqlite3.Row
        return conexao

    def _popular_a_partir_do_seed(self) -> None:
        if not self._caminho_seed_json.exists():
            return

        with open(self._caminho_seed_json, encoding="utf-8") as arquivo:
            corretores_seed = json.load(arquivo)

        linhas = [
            (
                item["id"],
                item["nome"],
                ",".join(item["zonas_atuacao"]),
                item.get("email"),
                item.get("telefone"),
            )
            for item in corretores_seed
        ]

        with self._conectar() as conexao:
            conexao.executemany(
                "INSERT OR IGNORE INTO corretores (id, nome, zonas_atuacao, email, telefone) "
                "VALUES (?, ?, ?, ?, ?)",
                linhas,
            )

    @staticmethod
    def _linha_para_corretor(linha: sqlite3.Row) -> Corretor:
        return Corretor(
            id=linha["id"],
            nome=linha["nome"],
            zonas_atuacao=linha["zonas_atuacao"].split(","),
            email=linha["email"],
            telefone=linha["telefone"],
        )

    def listar_todos(self) -> list[Corretor]:
        with self._conectar() as conexao:
            linhas = conexao.execute("SELECT * FROM corretores ORDER BY nome").fetchall()
        return [self._linha_para_corretor(linha) for linha in linhas]

    def buscar_por_id(self, corretor_id: str) -> Optional[Corretor]:
        with self._conectar() as conexao:
            linha = conexao.execute(
                "SELECT * FROM corretores WHERE id = ?", (corretor_id,)
            ).fetchone()
        return self._linha_para_corretor(linha) if linha else None

    def buscar_por_zona(self, zona: str) -> list[Corretor]:
        # zonas_atuacao é "Zona Sul,Zona Oeste" — LIKE cobre o caso de um
        # corretor com mais de uma zona cadastrada.
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT * FROM corretores WHERE zonas_atuacao LIKE ? ORDER BY nome",
                (f"%{zona}%",),
            ).fetchall()
        return [self._linha_para_corretor(linha) for linha in linhas]
