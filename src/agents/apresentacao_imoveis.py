"""Lista de imóveis no formato que o corretor usaria (montada em código).

Cada imóvel vira um resumo curto e padronizado, numerado, para o lead
escolher ("fotos do 2", "detalhes do 1"):

    Investimento: tipo · quartos (suítes) · vagas · bairro · valor · retorno mensal e anual
    Aluguel:      tipo · quartos (suítes) · vagas · bairro · aluguel · condomínio
    Compra:       tipo · quartos (suítes) · vagas · bairro · valor de venda · condomínio

As fotos NÃO são enviadas junto: o agente pergunta se o lead quer ver e de
qual imóvel. Os números (retorno, valores) vêm de Python, nunca do LLM.
"""
from __future__ import annotations

from src.domain.entities import Imovel
from src.domain.investimento import IndicadoresMercado, analisar_investimento, formatar_moeda, formatar_percentual

PERGUNTA_FOTOS = 'Quer ver as fotos ou mais detalhes de algum? É só me dizer o número (ex.: "fotos do 1").'


def _quartos(im: Imovel) -> str:
    if im.tipo_imovel in ("Studio", "Kitnet", "Sala comercial") or im.quartos == 0:
        return f"{im.metragem:.0f} m²"
    texto = f"{im.quartos} quarto{'s' if im.quartos > 1 else ''}"
    if im.suites:
        texto += f" ({im.suites} suíte{'s' if im.suites > 1 else ''})"
    return f"{texto} · {im.metragem:.0f} m²"


def _vagas(im: Imovel) -> str:
    return "sem vaga" if not im.vagas else f"{im.vagas} vaga{'s' if im.vagas > 1 else ''}"


def _condominio(im: Imovel) -> str:
    return f"Condomínio: {formatar_moeda(im.condominio)}" if im.condominio else "Sem condomínio"


def resumo_imovel(im: Imovel, intencao: str, indicadores: IndicadoresMercado | None = None) -> str:
    cabecalho = f"{im.tipo_imovel} · {_quartos(im)} · {_vagas(im)} · {im.bairro}"
    if intencao == "investimento" and im.tipo_negocio == "venda":
        kwargs = {"indicadores": indicadores} if indicadores else {}
        analise = analisar_investimento(im.preco, None, im.metragem, im.bairro, **kwargs)
        mensal = analise.rentabilidade_aa / 12
        valores = (
            f"Valor: {formatar_moeda(im.preco)} · Retorno estimado: {formatar_moeda(analise.aluguel_estimado)}/mês "
            f"({formatar_percentual(mensal)} ao mês · {formatar_percentual(analise.rentabilidade_aa)} ao ano)"
        )
    elif im.tipo_negocio == "aluguel":
        valores = f"Aluguel: {formatar_moeda(im.preco)}/mês · {_condominio(im)}"
    else:
        valores = f"Valor de venda: {formatar_moeda(im.preco)} · {_condominio(im)}"
    return f"{cabecalho}\n   {valores}"


def lista_numerada(imoveis: list[Imovel], intencao: str, indicadores: IndicadoresMercado | None = None) -> str:
    return "\n\n".join(f"{i}) {resumo_imovel(im, intencao, indicadores)}" for i, im in enumerate(imoveis, start=1))
