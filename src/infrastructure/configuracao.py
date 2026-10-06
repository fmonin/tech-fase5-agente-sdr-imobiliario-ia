"""Fontes de configuração (adaptadores de infraestrutura).

Cada fonte sabe LER um lugar e devolver um dicionário plano
{"secao.chave": valor} — e só isso (Single Responsibility). Quem decide a
ORDEM de precedência e monta o objeto tipado `Settings` é `src/config.py`.

Fontes disponíveis:
    • ArquivoTomlFonte  -> config/settings.toml (vai para o Git) e
                           config/settings.local.toml (ajustes desta máquina,
                           fora do Git)
    • AmbienteFonte     -> variáveis de ambiente (inclui o .env, que guarda
                           SOMENTE os segredos: chaves e tokens)

Para ler de outro lugar (ex.: Azure Key Vault, Azure App Configuration),
basta criar outra classe com o método `ler()` — nenhuma das existentes muda
(Open/Closed) e `Settings` continua igual (Dependency Inversion: o app
depende do contrato `FonteConfiguracao`, não de "onde" o valor está).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Protocol

try:  # Python 3.11+ traz o leitor de TOML na biblioteca padrão
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


class FonteConfiguracao(Protocol):
    nome: str

    def ler(self) -> dict[str, Any]:
        """Valores encontrados, com chaves no formato "secao.chave"."""
        ...


class ArquivoTomlFonte:
    def __init__(self, caminho: str | Path, obrigatorio: bool = False) -> None:
        self.caminho = Path(caminho)
        self.nome = self.caminho.name
        self._obrigatorio = obrigatorio

    def ler(self) -> dict[str, Any]:
        if not self.caminho.exists():
            if self._obrigatorio:
                raise FileNotFoundError(f"Arquivo de configuração não encontrado: {self.caminho}")
            return {}
        with open(self.caminho, "rb") as arquivo:
            return _achatar(tomllib.load(arquivo))


class AmbienteFonte:
    """Variáveis de ambiente. `nomes` = {"secao.chave": "NOME_DA_VARIAVEL"}."""

    nome = "variáveis de ambiente/.env"

    def __init__(self, nomes: Mapping[str, str], ambiente: Mapping[str, str] | None = None) -> None:
        self._nomes = nomes
        self._ambiente = ambiente if ambiente is not None else os.environ

    def ler(self) -> dict[str, Any]:
        return {chave: self._ambiente[var] for chave, var in self._nomes.items() if var in self._ambiente}


def _achatar(dados: Mapping[str, Any], prefixo: str = "") -> dict[str, Any]:
    plano: dict[str, Any] = {}
    for chave, valor in dados.items():
        caminho = f"{prefixo}{chave}"
        if isinstance(valor, Mapping):
            plano.update(_achatar(valor, f"{caminho}."))
        else:
            plano[caminho] = valor
    return plano
