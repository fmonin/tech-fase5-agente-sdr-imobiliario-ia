"""Testes da análise financeira de investimento (cálculo feito em Python,
não pelo LLM), dos indicadores de mercado e da formatação de moeda."""
import json

import pytest

from src.domain.investimento import (
    INDICADORES_PADRAO,
    analisar_investimento,
    formatar_moeda,
    interpretar_expectativa_retorno,
)
from src.infrastructure.mercado.json_market_data_repository import JsonMarketDataRepository


def test_formatar_moeda_padrao_brasileiro():
    assert formatar_moeda(480000) == "R$ 480.000,00"
    assert formatar_moeda(1920.5) == "R$ 1.920,50"


@pytest.mark.parametrize(
    "texto, pct",
    [("5% ao mês", 0.05), ("5", 0.05), ("0,5%", 0.005), ("0.5% a.m.", 0.005), ("6% ao ano", 0.005)],
)
def test_interpreta_percentual(texto, pct):
    assert interpretar_expectativa_retorno(texto).percentual_mensal == pytest.approx(pct)


@pytest.mark.parametrize("texto", ["R$ 3.000 por mês", "3000", "3 mil"])
def test_interpreta_valor_em_reais(texto):
    assert interpretar_expectativa_retorno(texto).valor_mensal == pytest.approx(3000)


def test_bairro_fora_do_indice_usa_rentabilidade_da_cidade():
    # Ipiranga não está na tabela -> 6,42% a.a. sobre o preço
    analise = analisar_investimento(480000, "5% ao mês", metragem=70, bairro="Ipiranga")
    assert analise.aluguel_estimado == pytest.approx(480000 * 0.0642 / 12)
    assert analise.aluguel_esperado == pytest.approx(24000)
    assert analise.classificacao.startswith("acima do mercado")
    texto = analise.descrever()
    assert "R$ 24.000,00" in texto
    assert "CDI 13,65%" in texto and "FipeZAP" in texto


def test_bairro_do_indice_usa_aluguel_por_m2():
    # Vila Mariana: R$ 72,40/m² x 62 m²
    analise = analisar_investimento(620000, None, metragem=62, bairro="vila mariana")
    assert analise.aluguel_estimado == pytest.approx(72.4 * 62)
    assert analise.valorizacao_12m == pytest.approx(0.002)
    assert "Expectativa do lead" not in analise.descrever()


def test_expectativa_realista():
    analise = analisar_investimento(480000, "0,53%", metragem=70, bairro="Ipiranga")
    assert analise.classificacao.startswith("dentro do mercado")


def test_poupanca_com_selic_alta_rende_meio_por_cento_ao_mes():
    assert INDICADORES_PADRAO.poupanca_aa == pytest.approx(1.005**12 - 1)


def test_repositorio_json_le_arquivo_do_projeto():
    ind = JsonMarketDataRepository("data/mercado_investimento.json").obter_indicadores()
    assert 0 < ind.selic_aa < 1 and 0 < ind.rentabilidade_aluguel_cidade_aa < 1
    assert ind.dados_bairro("Moema") is not None


def test_repositorio_json_invalido_usa_padrao(tmp_path):
    arquivo = tmp_path / "mercado.json"
    arquivo.write_text(json.dumps({"juros": {}}), encoding="utf-8")
    assert JsonMarketDataRepository(str(arquivo)).obter_indicadores() is INDICADORES_PADRAO
