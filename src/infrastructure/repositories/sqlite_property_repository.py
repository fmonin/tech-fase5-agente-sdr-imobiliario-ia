"""Repositório de imóveis com consulta em SQL (SQLite).

Implementa `IPropertyRepository` — repositório padrão da aplicação.
Diferente do `JsonPropertyRepository`, as buscas acontecem com **SQL real**
(`SELECT ... WHERE ...`), não com filtros em listas Python.

Inicialização:
    Na primeira execução, este repositório cria o banco SQLite (`data/imoveis.db`)
    e o popula a partir de `data/imoveis.json` — um arquivo editável que
    funciona como "dado fonte". A partir daí, toda consulta roda contra
    esse banco como SQL de verdade.

Separação entre dado fonte (JSON) e banco consultável (SQLite):
    Permite que a base de imóveis seja facilmente editada (JSON é legível),
    enquanto o sistema demonstra consultas SQL reais. Melhor dos dois mundos
    para fins didáticos e educacionais.
    
    Sincronização: a cada inicialização os imóveis do JSON são gravados no
    banco (insere os novos e atualiza os existentes pelo id). Assim, editar
    `imoveis.json` (ex.: incluir imóveis ou fotos) não exige apagar o .db.

Arquitetura:
    Nenhuma outra parte do sistema (agentes, serviços) sabe que SQLite está
    sendo usado — eles veem apenas a interface `IPropertyRepository`. Isso
    é o Dependency Inversion Principle em ação: trocar a implementação não
    quebra nada.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from src.infrastructure.sqlite_conexao import conectar
from src.domain.entities import Imovel
from src.domain.interfaces import IPropertyRepository

_CRIAR_TABELA = """
CREATE TABLE IF NOT EXISTS imoveis (
    id TEXT PRIMARY KEY,
    titulo TEXT NOT NULL,
    tipo_negocio TEXT NOT NULL,
    finalidade_investimento INTEGER NOT NULL,
    zona TEXT NOT NULL,
    bairro TEXT NOT NULL,
    preco REAL NOT NULL,
    quartos INTEGER NOT NULL,
    metragem REAL NOT NULL,
    descricao TEXT NOT NULL,
    fotos TEXT NOT NULL DEFAULT '[]',
    tipo_imovel TEXT NOT NULL DEFAULT 'Apartamento',
    suites INTEGER NOT NULL DEFAULT 0,
    vagas INTEGER NOT NULL DEFAULT 0,
    condominio REAL
)
"""

# Colunas incluídas depois da 1ª versão (bancos antigos ganham na inicialização).
_COLUNAS_NOVAS = {
    "fotos": "TEXT NOT NULL DEFAULT '[]'",
    "tipo_imovel": "TEXT NOT NULL DEFAULT 'Apartamento'",
    "suites": "INTEGER NOT NULL DEFAULT 0",
    "vagas": "INTEGER NOT NULL DEFAULT 0",
    "condominio": "REAL",
    "cadastrado_em": "TEXT",
    "cadastrado_por": "TEXT",
}

_COLUNAS = (
    "id",
    "titulo",
    "tipo_negocio",
    "finalidade_investimento",
    "zona",
    "bairro",
    "preco",
    "quartos",
    "metragem",
    "descricao",
    "fotos",
    "tipo_imovel",
    "suites",
    "vagas",
    "condominio",
)


class SqlitePropertyRepository(IPropertyRepository):
    def __init__(
        self,
        caminho_db: str | Path = "data/imoveis.db",
        caminho_seed_json: str | Path = "data/imoveis.json",
    ) -> None:
        self._caminho_db = Path(caminho_db)
        self._caminho_db.parent.mkdir(parents=True, exist_ok=True)
        self._caminho_seed_json = Path(caminho_seed_json)

        with self._conectar() as conexao:
            conexao.execute(_CRIAR_TABELA)
            colunas = {linha["name"] for linha in conexao.execute("PRAGMA table_info(imoveis)")}
            for coluna, tipo in _COLUNAS_NOVAS.items():
                if coluna not in colunas:
                    conexao.execute(f"ALTER TABLE imoveis ADD COLUMN {coluna} {tipo}")

        self._popular_a_partir_do_seed()

    def _conectar(self):
        return conectar(self._caminho_db, linhas_como_dict=True)

    def _popular_a_partir_do_seed(self) -> None:
        if not self._caminho_seed_json.exists():
            return

        with open(self._caminho_seed_json, encoding="utf-8") as arquivo:
            imoveis_seed = json.load(arquivo)

        linhas = [
            (
                item["id"],
                item["titulo"],
                item["tipo_negocio"],
                int(bool(item["finalidade_investimento"])),
                item["zona"],
                item["bairro"],
                item["preco"],
                item["quartos"],
                item["metragem"],
                item["descricao"],
                json.dumps(item.get("fotos", []), ensure_ascii=False),
                item.get("tipo_imovel", "Apartamento"),
                int(item.get("suites", 0)),
                int(item.get("vagas", 0)),
                item.get("condominio"),
            )
            for item in imoveis_seed
        ]

        with self._conectar() as conexao:
            conexao.executemany(
                f"INSERT INTO imoveis ({', '.join(_COLUNAS)}) "
                f"VALUES ({', '.join(['?'] * len(_COLUNAS))}) "
                f"ON CONFLICT(id) DO UPDATE SET "
                + ", ".join(f"{c} = excluded.{c}" for c in _COLUNAS if c != "id"),
                linhas,
            )

    @staticmethod
    def _linha_para_imovel(linha: sqlite3.Row) -> Imovel:
        return Imovel(
            id=linha["id"],
            titulo=linha["titulo"],
            tipo_negocio=linha["tipo_negocio"],
            finalidade_investimento=bool(linha["finalidade_investimento"]),
            zona=linha["zona"],
            bairro=linha["bairro"],
            preco=linha["preco"],
            quartos=linha["quartos"],
            metragem=linha["metragem"],
            descricao=linha["descricao"],
            fotos=json.loads(linha["fotos"] or "[]"),
            tipo_imovel=linha["tipo_imovel"],
            suites=linha["suites"],
            vagas=linha["vagas"],
            condominio=linha["condominio"],
            cadastrado_em=linha["cadastrado_em"],
            cadastrado_por=linha["cadastrado_por"],
        )

    def adicionar(self, imovel: Imovel) -> Imovel:
        """Cadastra um imóvel novo (ex.: pelo corretor). Se vier sem id, gera
        o próximo (IM042, IM043...). Fica só no banco — o JSON é o dado fonte
        da base inicial."""
        if not imovel.id:
            numeros = [int(i.id[2:]) for i in self.listar_todos() if i.id[:2] == "IM" and i.id[2:].isdigit()]
            imovel.id = f"IM{(max(numeros) + 1) if numeros else 1:03d}"
        colunas = _COLUNAS + ("cadastrado_em", "cadastrado_por")
        valores = (
            imovel.id, imovel.titulo, imovel.tipo_negocio, int(imovel.finalidade_investimento), imovel.zona,
            imovel.bairro, imovel.preco, imovel.quartos, imovel.metragem, imovel.descricao,
            json.dumps(imovel.fotos, ensure_ascii=False), imovel.tipo_imovel, imovel.suites, imovel.vagas,
            imovel.condominio, imovel.cadastrado_em, imovel.cadastrado_por,
        )
        with self._conectar() as conexao:
            conexao.execute(
                f"INSERT OR REPLACE INTO imoveis ({', '.join(colunas)}) VALUES ({', '.join(['?'] * len(colunas))})",
                valores,
            )
        return imovel

    def listar_todos(self) -> list[Imovel]:
        with self._conectar() as conexao:
            linhas = conexao.execute("SELECT * FROM imoveis").fetchall()
        return [self._linha_para_imovel(linha) for linha in linhas]

    def buscar(
        self,
        tipo_negocio: Optional[str] = None,
        zona: Optional[str] = None,
        preco_max: Optional[float] = None,
        preco_min: Optional[float] = None,
        quartos_min: Optional[int] = None,
    ) -> list[Imovel]:
        condicoes: list[str] = []
        parametros: list = []

        if tipo_negocio:
            condicoes.append("tipo_negocio = ?")
            parametros.append(tipo_negocio)
        if zona:
            condicoes.append("(LOWER(zona) LIKE ? OR LOWER(bairro) LIKE ?)")
            termo = f"%{zona.lower()}%"
            parametros.extend([termo, termo])
        if preco_max is not None:
            condicoes.append("preco <= ?")
            parametros.append(preco_max)
        if preco_min is not None:
            condicoes.append("preco >= ?")
            parametros.append(preco_min)
        if quartos_min is not None:
            condicoes.append("quartos >= ?")
            parametros.append(quartos_min)

        consulta = "SELECT * FROM imoveis"
        if condicoes:
            consulta += " WHERE " + " AND ".join(condicoes)

        with self._conectar() as conexao:
            linhas = conexao.execute(consulta, parametros).fetchall()

        return [self._linha_para_imovel(linha) for linha in linhas]
