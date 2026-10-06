"""Estimativa PRELIMINAR de valor do imóvel que o cliente quer vender ou
colocar para alugar (captação).

Regra de negócio pura (sem LLM, sem I/O), a partir de:
  1. imóveis parecidos da nossa base (mesmo negócio, mesmo tipo, quartos
     próximos) — primeiro no mesmo bairro, depois vizinhos, depois a zona;
  2. o índice de mercado do bairro (R$/m² de venda ou de aluguel — FipeZAP
     e outras fontes em data/mercado_investimento.json), quando há metragem.

O resultado é uma FAIXA, nunca um valor fechado: estado de conservação,
andar, vista e documentação só o corretor avalia na visita.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Optional

from src.domain.entities import Imovel
from src.domain.investimento import IndicadoresMercado, formatar_moeda
from src.domain.localizacao import MapaBairros

_FAMILIA = {"Apartamento": "apto", "Cobertura": "apto", "Studio": "apto", "Kitnet": "apto",
            "Casa": "casa", "Sobrado": "casa", "Sala comercial": "comercial", "Loja": "comercial"}


@dataclass
class EstimativaValor:
    minimo: float
    maximo: float
    referencia: str
    comparaveis: list[Imovel]

    def texto(self, finalidade: str) -> str:
        sufixo = "/mês" if finalidade == "locacao" else ""
        return (f"entre {formatar_moeda(self.minimo)}{sufixo} e {formatar_moeda(self.maximo)}{sufixo} "
                f"({self.referencia})")


def estimar_valor(
    finalidade: str,
    tipo: Optional[str],
    quartos: Optional[int],
    metragem: Optional[float],
    bairro: Optional[str],
    imoveis: list[Imovel],
    mapa: Optional[MapaBairros] = None,
    indicadores: Optional[IndicadoresMercado] = None,
) -> Optional[EstimativaValor]:
    negocio = "aluguel" if finalidade == "locacao" else "venda"
    familia = _FAMILIA.get(tipo or "", "apto")
    base = [im for im in imoveis if im.tipo_negocio == negocio and _FAMILIA.get(im.tipo_imovel, "apto") == familia
            and (quartos is None or abs(im.quartos - quartos) <= 1)]

    comparaveis, onde = [], ""
    if bairro:
        vizinhos = {v.lower() for v in mapa.vizinhos_de(bairro)} if mapa else set()
        zona = (mapa.zona_de(bairro) or "").lower() if mapa else ""
        for filtro, descricao in (
            (lambda im: im.bairro.lower() == bairro.lower(), f"em {bairro}"),
            (lambda im: im.bairro.lower() in vizinhos, f"em bairros vizinhos de {bairro}"),
            (lambda im: zona and im.zona.lower() == zona, f"na {zona.title()}"),
        ):
            achados = [im for im in base if filtro(im)]
            if achados and not comparaveis:
                comparaveis, onde = achados, descricao  # guarda o 1º nível com algum imóvel
            if len(achados) >= 2:
                comparaveis, onde = achados, descricao
                break
    partes: list[str] = []
    valores: list[float] = []

    m2_mercado = None
    if indicadores and bairro and metragem:
        dados = indicadores.dados_bairro(bairro)
        if dados:
            m2_mercado = dados.aluguel_m2 if negocio == "aluguel" else dados.venda_m2
            if m2_mercado:
                valores.append(m2_mercado * metragem)
                fonte = dados.fonte or "índice FipeZAP"
                partes.append(f"{fonte}: {formatar_moeda(m2_mercado)}/m² em {bairro}")

    if comparaveis:
        if metragem:
            precos_m2 = [im.preco / im.metragem for im in comparaveis if im.metragem]
            if precos_m2:
                valores.append(median(precos_m2) * metragem)
        else:
            mesmos = [im for im in comparaveis if quartos is None or im.quartos == quartos]
            precos = [im.preco for im in (mesmos if len(mesmos) >= 2 else comparaveis)]
            valores.append(median(precos))
        partes.insert(0, f"{len(comparaveis)} imóvel(is) parecido(s) da nossa base {onde}")

    if not valores:
        return None
    centro = median(valores)
    folga = 0.10 if metragem else 0.15  # sem metragem a incerteza é maior
    minimo = min(min(valores), centro * (1 - folga))
    maximo = max(max(valores), centro * (1 + folga))
    passo = 50 if negocio == "aluguel" else 5000  # arredonda para valores "de mercado"
    return EstimativaValor(round(minimo / passo) * passo, round(maximo / passo) * passo, "; ".join(partes), comparaveis)
