"""Pós-visita: o que o cliente achou do imóvel e como isso melhora as
próximas sugestões. Regras simples e determinísticas (sem LLM).

- `classificar_feedback`: "adorei!" -> positivo; "achei caro" -> negativo
  com motivo "Preço".
- `aplicar_feedback`: nas próximas buscas, tira os imóveis de que o cliente
  não gostou e ajusta pelo motivo (Preço -> mais baratos que o visitado;
  Tamanho -> maiores; Localização -> outro bairro). Se o ajuste esvaziar a
  lista, fica só a exclusão dos imóveis recusados (nunca "some" tudo).
"""
from __future__ import annotations

import re
import unicodedata

from src.domain.entities import Imovel

RESULTADOS_VISITA = {
    "gostou": "👍 Gostou — em negociação",
    "proposta": "📝 Fez proposta",
    "fechado": "🤝 Negócio fechado",
    "nao_gostou": "👎 Não gostou",
    "nao_compareceu": "🚫 Cliente não compareceu",
}
MOTIVOS_PERDA = ["Preço", "Localização", "Tamanho", "Estado do imóvel", "Condomínio caro", "Outro"]

_MOTIVOS_PALAVRAS = {
    "Preço": r"\b(car[oa]s?|preco|valor|salgad\w*|acima do (meu )?orcamento)\b",
    "Condomínio caro": r"\bcondominio\b",
    "Localização": r"\b(long[ei]|localiza\w*|bairro|regiao|transito|perigos\w*|barulh\w*|rua)\b",
    "Tamanho": r"\b(pequen\w*|apertad\w*|tamanho|espaco|minusculo|comod\w*)\b",
    "Estado do imóvel": r"\b(velh\w*|reforma\w*|estado|conservad\w*|umidade|mofo|antig\w*|estragad\w*)\b",
}
_NEGATIVO = re.compile(
    r"\b(nao gostei|nao gostamos|nao curti|nao curtimos|nao me agradou|nao agradou|ruim|horrivel|pessim\w*|"
    r"feio|decepcion\w*|nao e (pra|para) mim|nao serve|nao rolou|nao quero)\b"
)
_POSITIVO = re.compile(
    r"\b(gostei|gostamos|adorei|adoramos|amei|amamos|otim\w*|maravilhos\w*|perfeit\w*|lind\w*|bom|boa|"
    r"legal|interessad\w*|quero fechar|quero comprar|quero alugar|vamos fechar|curti|top)\b"
)


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def classificar_feedback(texto: str) -> tuple[str, str | None]:
    """('positivo'|'negativo'|'neutro', motivo ou None)."""
    t = _normalizar(texto)
    motivo = next((m for m, padrao in _MOTIVOS_PALAVRAS.items() if re.search(padrao, t)), None)
    if motivo == "Preço" and re.search(r"\bcondominio\b", t):
        motivo = "Condomínio caro"
    if _NEGATIVO.search(t) or (motivo and not _POSITIVO.search(t)):
        return "negativo", motivo
    if _POSITIVO.search(t):
        return "positivo", motivo  # ex.: "gostei, mas achei caro" -> positivo com ressalva
    return "neutro", motivo


def registro_de_feedback(imovel: Imovel | None, motivo: str | None, imovel_id: str | None = None) -> dict:
    return {
        "imovel_id": imovel.id if imovel else imovel_id,
        "motivo": motivo,
        "preco": imovel.preco if imovel else None,
        "metragem": imovel.metragem if imovel else None,
        "bairro": imovel.bairro if imovel else None,
    }


def aplicar_feedback(imoveis: list[Imovel], feedbacks: list[dict] | None) -> tuple[list[Imovel], list[str]]:
    """(imóveis ajustados, motivos considerados)."""
    if not feedbacks:
        return imoveis, []
    recusados = {f.get("imovel_id") for f in feedbacks}
    base = [im for im in imoveis if im.id not in recusados]
    ajustados = list(base)
    considerados: list[str] = []
    for f in feedbacks:
        motivo = f.get("motivo")
        if motivo in ("Preço", "Condomínio caro") and f.get("preco"):
            filtrados = [im for im in ajustados if im.preco < f["preco"]]
        elif motivo == "Tamanho" and f.get("metragem"):
            filtrados = [im for im in ajustados if im.metragem > f["metragem"]]
        elif motivo == "Localização" and f.get("bairro"):
            filtrados = [im for im in ajustados if im.bairro.lower() != f["bairro"].lower()]
        else:
            continue
        if filtrados:
            ajustados = filtrados
            considerados.append(motivo)
    return (ajustados or base), list(dict.fromkeys(considerados))
