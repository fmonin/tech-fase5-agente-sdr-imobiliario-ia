"""Regras para saber de QUAL imóvel o lead está falando.

Ex.: "quero mais informação do imóvel do Tatuapé", "me fala do garden",
"como é esse imóvel?", "quero visitar o da Casa Verde".

Antes não existia esse conceito: qualquer pergunta caía no Consultor de
Imóveis, que refazia a busca e repetia as mesmas sugestões genéricas em vez
de detalhar o imóvel pedido.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from src.domain.entities import Imovel

PADRAO_PEDIDO_DETALHES = re.compile(
    r"\b(mais )?(informac|informaç|detalh|descri)\w*|"
    r"\b(fal[ae]|conta|me diz|explica)\w* (mais )?(sobre|do|da|dele|dela)\b|"
    r"\bcomo (e|é) (o|a|esse|essa|este|esta)\b|"
    r"\bquero (saber|ver|conhecer) (mais )?(sobre )?(o|a|esse|essa|este|esta)\b|"
    r"\b(quantos m|metragem|tem vaga|tem garagem|condom[ií]nio|andar)\b|"
    r"\b(fotos?|imagens?|imagem)\b|\bver (o|a|esse|essa|este|esta) (im[oó]vel|apartamento|casa|studio)\b"
)
_PADRAO_REFERENCIA = re.compile(r"\b(esse|essa|este|esta|dele|dela|ele|ela|nele|nela)\b")
_PALAVRAS_IGNORADAS = {"apartamento", "imovel", "proximo", "investidor", "venda", "aluguel", "para", "com"}


def _norm(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def pediu_detalhes(texto: str) -> bool:
    return bool(PADRAO_PEDIDO_DETALHES.search(_norm(texto)) or PADRAO_PEDIDO_DETALHES.search(texto.lower()))


def candidatos_citados(texto: str, imoveis: list[Imovel]) -> list[Imovel]:
    """Imóveis que melhor batem com o texto (empatados no topo), pelo código
    (IM012), bairro e palavras do título ("garden", "cobertura", "casa")."""
    t = _norm(texto)
    pontos: dict[str, int] = {}
    for im in imoveis:
        p = 0
        if re.search(rf"\b{_norm(im.id)}\b", t):
            p += 10
        bairro = _norm(im.bairro)
        if re.search(rf"\b{re.escape(bairro)}\b", t):
            p += 5
        for palavra in re.findall(r"\w{4,}", _norm(im.titulo)):
            if palavra not in _PALAVRAS_IGNORADAS and palavra not in bairro.split() and re.search(rf"\b{palavra}\b", t):
                p += 2
        if p:
            pontos[im.id] = p
    if not pontos:
        return []
    melhor = max(pontos.values())
    return [im for im in imoveis if pontos.get(im.id) == melhor]


def ids_mostrados(texto_agente: str) -> list[str]:
    """Ids dos imóveis exibidos numa mensagem do agente (marcadores de foto)."""
    return re.findall(r"IM\d+", " ".join(re.findall(r"\[\[(?:FOTOS|CAPAS|LISTA):([^\]]+)\]\]", texto_agente)))


_PADRAO_NUMERO_DA_LISTA = re.compile(
    r"(?:\b(?:n[uú]mero|op[cç][aã]o|im[oó]vel|do|da|o|a|no|na)\s*|#|^\W*)(\d{1,2})\b(?!\s*(?:quarto|mil|m²|m2|%|vaga|su[ií]te|k\b))"
)
_ORDINAIS = {"primeiro": 1, "primeira": 1, "segundo": 2, "segunda": 2, "terceiro": 3, "terceira": 3}


def numero_da_lista(texto: str, total: int) -> int | None:
    """"fotos do 2", "quero ver o 1", "detalhes do terceiro" -> posição na lista."""
    t = _norm(texto)
    for palavra, n in _ORDINAIS.items():
        if re.search(rf"\b{palavra}\b", t) and n <= total:
            return n
    m = _PADRAO_NUMERO_DA_LISTA.search(t)
    if m and 1 <= int(m.group(1)) <= total:
        return int(m.group(1))
    return None


def resolver_imovel_citado(
    texto: str,
    imoveis: list[Imovel],
    foco_id: Optional[str] = None,
    mostrados: Optional[list[str]] = None,
) -> Optional[Imovel]:
    """Resolve o imóvel citado. Em empate (ex.: dois imóveis no Tatuapé),
    prefere o que o agente acabou de mostrar ao lead (`mostrados`) ou o que
    está em foco. Se o lead usar só uma referência ("esse imóvel", "dele"),
    usa o imóvel em foco."""
    candidatos = candidatos_citados(texto, imoveis)
    if len(candidatos) == 1:
        return candidatos[0]
    if len(candidatos) > 1:
        preferidos = [im for im in candidatos if im.id in (mostrados or []) or im.id == foco_id]
        return preferidos[0] if len(preferidos) == 1 else None
    if foco_id and _PADRAO_REFERENCIA.search(_norm(texto)):
        return next((im for im in imoveis if im.id == foco_id), None)
    return None
