"""Repositório de indicadores de mercado lido de um JSON atualizado à mão.

Arquivo: `data/mercado_investimento.json` (caminho configurável por
`[dados] mercado_json_path` em config/settings.toml). O JSON guarda taxas em % (ex.: 13.75) e este
repositório converte para fração (0.1375), que é como o domínio calcula.

Se o arquivo não existir ou estiver inválido, devolve `INDICADORES_PADRAO`
e registra um aviso — o agente nunca "quebra" por causa da tabela.
Trocar por uma API (ex.: Banco Central) no futuro = criar outra classe que
implemente `IMarketDataRepository`, sem mexer nos agentes (OCP/DIP).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from src.domain.interfaces import IMarketDataRepository
from src.domain.investimento import INDICADORES_PADRAO, DadosBairro, IndicadoresMercado

_log = logging.getLogger(__name__)


def _pct(valor) -> float | None:
    return None if valor is None else float(valor) / 100


class JsonMarketDataRepository(IMarketDataRepository):
    def __init__(self, caminho_json: str) -> None:
        self._caminho = Path(caminho_json)
        self._cache: IndicadoresMercado | None = None

    def obter_indicadores(self) -> IndicadoresMercado:
        if self._cache is None:
            self._cache = self._carregar()
        return self._cache

    def _carregar(self) -> IndicadoresMercado:
        try:
            dados = json.loads(self._caminho.read_text(encoding="utf-8"))
            juros, inflacao, fipe = dados["juros"], dados["inflacao"], dados["fipezap"]
            bairros = {
                nome: DadosBairro(
                    aluguel_m2=float(b["aluguel_m2"]),
                    venda_m2=b.get("venda_m2"),
                    variacao_aluguel_12m=_pct(b.get("variacao_aluguel_12m")),
                    variacao_venda_12m=_pct(b.get("variacao_venda_12m")),
                    fonte=b.get("fonte"),
                )
                for nome, b in dados.get("bairros", {}).items()
            }
            fontes = ("Copom/Banco Central (Selic)", "B3 (CDI)", "IBGE (IPCA)", "Índice FipeZAP")
            return IndicadoresMercado(
                data_referencia=dados.get("data_referencia", ""),
                cidade=dados.get("cidade", ""),
                selic_aa=_pct(juros["selic_aa"]),
                cdi_aa=_pct(juros["cdi_aa"]),
                ipca_12m=_pct(inflacao["ipca_12m"]),
                rentabilidade_aluguel_cidade_aa=_pct(fipe["rentabilidade_aluguel_cidade_aa"]),
                variacao_venda_cidade_12m=_pct(fipe.get("variacao_venda_cidade_12m")),
                referencia_fipezap=fipe.get("referencia", ""),
                venda_m2_cidade=fipe.get("venda_m2_cidade"),
                bairros=bairros,
                fontes=fontes,
            )
        except (OSError, KeyError, ValueError, TypeError) as erro:
            _log.warning(
                "Não foi possível ler %s (%s). Usando indicadores padrão.", self._caminho, erro
            )
            return INDICADORES_PADRAO
