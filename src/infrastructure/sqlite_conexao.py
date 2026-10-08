"""Conexão SQLite que sempre fecha o arquivo.

`with sqlite3.connect(...) as c` só faz commit/rollback: a conexão continua
aberta até o coletor de lixo agir. No Linux isso passa despercebido, mas no
Windows o arquivo fica travado ("WinError 32") e não pode ser apagado ou
movido. Aqui a conexão faz commit (ou rollback) e é fechada ao sair do bloco.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def conectar(caminho, *, linhas_como_dict: bool = False) -> Iterator[sqlite3.Connection]:
    conexao = sqlite3.connect(caminho)
    if linhas_como_dict:
        conexao.row_factory = sqlite3.Row
    try:
        yield conexao
        conexao.commit()
    except BaseException:
        conexao.rollback()
        raise
    finally:
        conexao.close()
