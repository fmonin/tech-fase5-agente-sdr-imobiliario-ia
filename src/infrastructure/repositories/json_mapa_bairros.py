"""Carrega `data/bairros_sp.json` em um `MapaBairros` (vazio se der erro)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from src.domain.localizacao import MapaBairros

_log = logging.getLogger(__name__)


def carregar_mapa_bairros(caminho: str | Path) -> MapaBairros:
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
        return MapaBairros.a_partir_de_dict(dados.get("bairros", {}))
    except (OSError, ValueError, AttributeError) as erro:
        _log.warning("Não foi possível ler %s (%s). Busca por proximidade desativada.", caminho, erro)
        return MapaBairros()
