"""Cálculos financeiros de investimento imobiliário (regras de domínio puras).

Por que existe:
    Ao ligar o Azure OpenAI, o agente passou a "fazer contas" sozinho na
    resposta (ex.: "5% ao mês sobre R$ 480.000 = R$ 24.000") e a inventar
    comparações (ex.: rendimento de fundos imobiliários) que não estão em
    nenhum dado do sistema. LLMs não são confiáveis para aritmética nem para
    números de mercado.

    Seguimos o mesmo princípio do `EsclarecedorAgent`: a DECISÃO/CÁLCULO é
    feita em Python, de forma determinística e testável, e o LLM só REDIGE
    a resposta usando os números prontos.

Os dados de mercado (Selic, CDI, IPCA, Índice FipeZAP) chegam em um
`IndicadoresMercado`, carregado pela infraestrutura a partir de
`data/mercado_investimento.json` (tabela atualizada manualmente). Assim o
LLM fala de dados REAIS e com fonte, sem inventar nada.

Sem dependências externas (camada de domínio da Clean Architecture).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Margem em torno do aluguel estimado para formar a "faixa de mercado"
# (o índice é uma média; cada imóvel varia com andar, vaga, estado etc.).
MARGEM_FAIXA_ALUGUEL = 0.10  # ±10%


def formatar_moeda(valor: float) -> str:
    """Formata no padrão brasileiro: 480000 -> 'R$ 480.000,00'."""
    texto = f"{valor:,.2f}"  # '480,000.00'
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def formatar_percentual(fracao: float) -> str:
    """0.005 -> '0,5%' ; 0.05 -> '5%'."""
    texto = f"{fracao * 100:.2f}".rstrip("0").rstrip(".")
    return f"{texto.replace('.', ',')}%"


@dataclass(frozen=True)
class ExpectativaRetorno:
    """Expectativa do lead já normalizada.

    `percentual_mensal` é uma fração (0.005 = 0,5% ao mês). Quando o lead
    informa um valor em reais (ex.: "R$ 3.000 por mês"), guardamos em
    `valor_mensal` e o percentual é calculado depois, sobre o preço do imóvel.
    """

    percentual_mensal: Optional[float] = None
    valor_mensal: Optional[float] = None
    convertido_de_anual: bool = False


_RE_NUMERO = r"(\d+(?:[.,]\d+)*)"


def _para_float(texto: str) -> float:
    # "3.000" / "3.000,50" (BR) ou "0.5" / "0,5"
    if "," in texto:
        return float(texto.replace(".", "").replace(",", "."))
    if texto.count(".") == 1 and len(texto.split(".")[1]) != 3:
        return float(texto)
    return float(texto.replace(".", ""))


def interpretar_expectativa_retorno(texto: Optional[str]) -> Optional[ExpectativaRetorno]:
    """Converte o texto livre de `expectativa_retorno` em números.

    Regras (simples e auditáveis):
      - "X%" -> percentual. Se o texto citar ano/anual/a.a., converte para
        mensal (divide por 12, aproximação simples). Caso contrário é mensal,
        que é como o agente pergunta ("qual retorno mensal você espera?").
      - Número sem "%" e >= 100 (ex.: "R$ 3.000", "3000 por mês") -> valor
        de aluguel mensal em reais.
    """
    if not texto:
        return None
    texto_lower = str(texto).lower()
    anual = bool(re.search(r"\bano\b|anual|a\.a\.|\baa\b|por ano|ao ano", texto_lower))

    m_pct = re.search(_RE_NUMERO + r"\s*%", texto_lower)
    if m_pct:
        pct = _para_float(m_pct.group(1)) / 100
        if anual:
            return ExpectativaRetorno(percentual_mensal=pct / 12, convertido_de_anual=True)
        return ExpectativaRetorno(percentual_mensal=pct)

    m_num = re.search(_RE_NUMERO, texto_lower)
    if m_num:
        valor = _para_float(m_num.group(1))
        if "mil" in texto_lower and valor < 1000:
            valor *= 1000
        if valor >= 100:
            if anual:
                return ExpectativaRetorno(valor_mensal=valor / 12, convertido_de_anual=True)
            return ExpectativaRetorno(valor_mensal=valor)
        # Número pequeno sem "%" (ex.: "5"): tratamos como percentual.
        pct = valor / 100
        if anual:
            return ExpectativaRetorno(percentual_mensal=pct / 12, convertido_de_anual=True)
        return ExpectativaRetorno(percentual_mensal=pct)
    return None


@dataclass(frozen=True)
class DadosBairro:
    aluguel_m2: float
    venda_m2: Optional[float] = None
    variacao_aluguel_12m: Optional[float] = None  # em %
    variacao_venda_12m: Optional[float] = None  # em %
    fonte: Optional[str] = None  # quando o dado não vem do Informe FipeZAP principal

    @property
    def rentabilidade_aa(self) -> Optional[float]:
        """Rentabilidade bruta anual do aluguel no bairro (fração)."""
        if not self.venda_m2:
            return None
        return self.aluguel_m2 * 12 / self.venda_m2


@dataclass(frozen=True)
class IndicadoresMercado:
    """Indicadores de mercado (taxas em FRAÇÃO ao ano: 0.1375 = 13,75% a.a.)."""

    data_referencia: str
    cidade: str
    selic_aa: float
    cdi_aa: float
    ipca_12m: float
    rentabilidade_aluguel_cidade_aa: float
    variacao_venda_cidade_12m: Optional[float] = None
    referencia_fipezap: str = ""
    venda_m2_cidade: Optional[float] = None  # R$/m² médio de venda (FipeZAP)
    bairros: dict[str, DadosBairro] = field(default_factory=dict)
    fontes: tuple[str, ...] = ()

    @property
    def poupanca_aa(self) -> float:
        """Regra oficial da poupança: com Selic > 8,5% a.a. rende 0,5% ao mês
        (+ TR); caso contrário, 70% da Selic (+ TR). Valor sem a TR."""
        if self.selic_aa > 0.085:
            return (1.005 ** 12) - 1
        return self.selic_aa * 0.7

    def dados_bairro(self, bairro: Optional[str]) -> Optional[DadosBairro]:
        if not bairro:
            return None
        chave = bairro.strip().lower()
        for nome, dados in self.bairros.items():
            if nome.lower() == chave:
                return dados
        return None


# Valores de fallback (mesmos de data/mercado_investimento.json em 03/10/2026),
# usados nos testes e se o arquivo JSON não puder ser lido.
INDICADORES_PADRAO = IndicadoresMercado(
    data_referencia="setembro/2026",
    cidade="São Paulo",
    selic_aa=0.1375,
    cdi_aa=0.1365,
    ipca_12m=0.0422,
    rentabilidade_aluguel_cidade_aa=0.0642,
    variacao_venda_cidade_12m=0.0363,
    referencia_fipezap="agosto/2026",
    venda_m2_cidade=12143.0,
    bairros={
        "Vila Mariana": DadosBairro(72.4, 14806, -0.026, 0.002),
        "Moema": DadosBairro(84.8, 16423, 0.045, 0.051),
        "Bela Vista": DadosBairro(74.4, 12515, 0.031, 0.028),
    },
    fontes=("Copom/Banco Central", "B3 (CDI)", "IBGE (IPCA)", "Índice FipeZAP"),
)


@dataclass(frozen=True)
class AnaliseInvestimento:
    preco: float
    aluguel_estimado: float
    aluguel_mercado_min: float
    aluguel_mercado_max: float
    rentabilidade_aa: float
    origem_estimativa: str
    indicadores: IndicadoresMercado
    percentual_esperado: Optional[float] = None  # fração ao mês
    aluguel_esperado: Optional[float] = None
    classificacao: str = "sem expectativa"
    valorizacao_12m: Optional[float] = None
    origem_valorizacao: str = ""

    @property
    def rentabilidade_real_aa(self) -> float:
        """Rentabilidade do aluguel descontada a inflação (IPCA)."""
        return (1 + self.rentabilidade_aa) / (1 + self.indicadores.ipca_12m) - 1

    def descrever(self) -> str:
        """Texto pronto (números já calculados) para o prompt do LLM."""
        ind = self.indicadores
        linhas = [
            f"Preço: {formatar_moeda(self.preco)}",
            (
                f"Aluguel de mercado estimado: {formatar_moeda(self.aluguel_mercado_min)} a "
                f"{formatar_moeda(self.aluguel_mercado_max)} por mês ({self.origem_estimativa})"
            ),
            (
                f"Rentabilidade bruta do aluguel: {formatar_percentual(self.rentabilidade_aa)} ao ano "
                f"(real, descontado o IPCA de {formatar_percentual(ind.ipca_12m)}: "
                f"{formatar_percentual(self.rentabilidade_real_aa)} ao ano)"
            ),
        ]
        if self.valorizacao_12m is not None:
            linhas.append(
                f"Valorização do m² em 12 meses: {formatar_percentual(self.valorizacao_12m)} "
                f"({self.origem_valorizacao})"
            )
        linhas.append(
            "Comparação (bruto, ao ano): "
            f"CDI {formatar_percentual(ind.cdi_aa)}; "
            f"Selic {formatar_percentual(ind.selic_aa)}; "
            f"poupança {formatar_percentual(ind.poupanca_aa)} + TR; "
            f"aluguel deste imóvel {formatar_percentual(self.rentabilidade_aa)}"
        )
        if self.percentual_esperado is not None and self.aluguel_esperado is not None:
            linhas.append(
                "Expectativa do lead: "
                f"{formatar_percentual(self.percentual_esperado)} ao mês = "
                f"{formatar_moeda(self.aluguel_esperado)} de aluguel por mês"
            )
            linhas.append(f"Avaliação: expectativa {self.classificacao}")
        linhas.append(f"Dados de mercado de {ind.data_referencia}; fontes: {', '.join(ind.fontes)}")
        return " | ".join(linhas)


def analisar_investimento(
    preco: float,
    expectativa: Optional[str],
    metragem: Optional[float] = None,
    bairro: Optional[str] = None,
    indicadores: IndicadoresMercado = INDICADORES_PADRAO,
) -> AnaliseInvestimento:
    """Calcula a análise do investimento com dados de mercado reais.

    Aluguel estimado:
      1) se o bairro está no Índice FipeZAP: R$/m² de aluguel do bairro x metragem;
      2) senão: rentabilidade média da cidade (FipeZAP) x preço / 12.
    """
    dados_bairro = indicadores.dados_bairro(bairro)
    ref = indicadores.referencia_fipezap or indicadores.data_referencia
    if dados_bairro and metragem:
        aluguel = dados_bairro.aluguel_m2 * metragem
        origem = (
            f"{dados_bairro.fonte or f'FipeZAP {ref}'}: aluguel médio de "
            f"{formatar_moeda(dados_bairro.aluguel_m2)}/m² em {bairro}"
        )
    else:
        aluguel = preco * indicadores.rentabilidade_aluguel_cidade_aa / 12
        origem = (
            f"FipeZAP {ref}: rentabilidade média de "
            f"{formatar_percentual(indicadores.rentabilidade_aluguel_cidade_aa)} ao ano "
            f"em {indicadores.cidade}"
        )

    if dados_bairro and dados_bairro.variacao_venda_12m is not None:
        valorizacao, origem_val = dados_bairro.variacao_venda_12m, f"FipeZAP, {bairro}"
    else:
        valorizacao, origem_val = indicadores.variacao_venda_cidade_12m, f"FipeZAP, média de {indicadores.cidade}"

    faixa_min = aluguel * (1 - MARGEM_FAIXA_ALUGUEL)
    faixa_max = aluguel * (1 + MARGEM_FAIXA_ALUGUEL)

    exp = interpretar_expectativa_retorno(expectativa)
    percentual = aluguel_esp = None
    if exp is not None and preco > 0:
        if exp.percentual_mensal is not None:
            percentual = exp.percentual_mensal
            aluguel_esp = preco * percentual
        elif exp.valor_mensal is not None:
            aluguel_esp = exp.valor_mensal
            percentual = aluguel_esp / preco

    if aluguel_esp is None:
        classificacao = "sem expectativa"
    elif aluguel_esp > faixa_max:
        classificacao = "acima do mercado (dificilmente alcançável só com aluguel)"
    elif aluguel_esp < faixa_min:
        classificacao = "abaixo do mercado (conservadora, facilmente alcançável)"
    else:
        classificacao = "dentro do mercado (realista)"

    return AnaliseInvestimento(
        preco=preco,
        aluguel_estimado=aluguel,
        aluguel_mercado_min=faixa_min,
        aluguel_mercado_max=faixa_max,
        rentabilidade_aa=aluguel * 12 / preco if preco else 0.0,
        origem_estimativa=origem,
        indicadores=indicadores,
        percentual_esperado=percentual,
        aluguel_esperado=aluguel_esp,
        classificacao=classificacao,
        valorizacao_12m=valorizacao,
        origem_valorizacao=origem_val,
    )
