"""Repositório de imóveis baseado em um arquivo JSON.

⚠️ Mantido no projeto como MATERIAL DE REFERÊNCIA, para mostrar na prática
o Open/Closed + Liskov Substitution do SOLID: esta classe e a
`SqlitePropertyRepository` (usada por padrão pela aplicação, veja
`src/container.py`) implementam a MESMA interface (`IPropertyRepository`)
de formas completamente diferentes (filtro em lista Python vs. `SELECT`
SQL) e são 100% intercambiáveis — dá para trocar uma pela outra em
`container.py` sem tocar em nenhum agente ou serviço.

Implementa `IPropertyRepository`. Em um projeto real, esta classe seria
trocada por uma que consulta um banco de dados (Postgres, Cosmos DB, etc.).
Como as camadas superiores dependem apenas da interface
(`IPropertyRepository`), essa troca não afeta o restante do sistema — este
é o Open/Closed Principle em ação.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.domain.entities import Imovel
from src.domain.interfaces import IPropertyRepository


class JsonPropertyRepository(IPropertyRepository):
    def __init__(self, caminho_arquivo: str | Path = "data/imoveis.json") -> None:
        self._caminho = Path(caminho_arquivo)
        self._imoveis: list[Imovel] = self._carregar()

    def _carregar(self) -> list[Imovel]:
        if not self._caminho.exists():
            return []
        with open(self._caminho, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        return [Imovel(**item) for item in dados]

    def listar_todos(self) -> list[Imovel]:
        return list(self._imoveis)

    def buscar(
        self,
        tipo_negocio: Optional[str] = None,
        zona: Optional[str] = None,
        preco_max: Optional[float] = None,
        preco_min: Optional[float] = None,
        quartos_min: Optional[int] = None,
    ) -> list[Imovel]:
        resultado = self._imoveis

        if tipo_negocio:
            resultado = [im for im in resultado if im.tipo_negocio == tipo_negocio]
        if zona:
            zona_lower = zona.lower()
            resultado = [
                im for im in resultado
                if zona_lower in im.zona.lower() or zona_lower in im.bairro.lower()
            ]
        if preco_max is not None:
            resultado = [im for im in resultado if im.preco <= preco_max]
        if preco_min is not None:
            resultado = [im for im in resultado if im.preco >= preco_min]
        if quartos_min is not None:
            resultado = [im for im in resultado if im.quartos >= quartos_min]

        return resultado
