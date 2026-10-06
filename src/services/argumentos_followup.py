"""Argumentos de negócio para o follow-up (fatos REAIS, calculados em Python).

Quando o cliente parou no meio de uma compra, aluguel, investimento ou da
venda/locação do próprio imóvel — inclusive se ele encerrou a conversa —, o
follow-up tenta trazê-lo de volta mostrando por que vale a pena continuar.
Para não "inventar" vantagens, os argumentos são fatos da nossa base:

  • compra/aluguel: quantos imóveis combinam com a busca e o melhor deles;
  • investimento: rentabilidade estimada do aluguel × poupança (dados de
    mercado) no imóvel de maior retorno dentro do orçamento;
  • captação (vender/alugar o próprio imóvel): quantos clientes procuram
    imóvel na região dele, a estimativa de valor e a avaliação sem custo.

O LLM só transforma esses fatos numa mensagem simpática e persuasiva.
"""
from __future__ import annotations

from typing import Optional

from src.agents.followup_agent import tem_agendamento
from src.domain.entities import Lead
from src.domain.investimento import analisar_investimento, formatar_moeda, formatar_percentual


def assunto_pendente(lead: Lead) -> Optional[str]:
    """'captacao' | 'compra' | 'aluguel' | 'investimento' | None."""
    if lead.captacao:
        return "captacao"
    intencao = lead.perfil.intencao.value
    if intencao != "indefinida" and not tem_agendamento(lead):
        return intencao
    return None


class ArgumentosFollowUp:
    def __init__(self, repositorio_imoveis, lead_repository, match, carteira, dados_mercado=None) -> None:
        self._imoveis = repositorio_imoveis
        self._leads = lead_repository
        self._match = match
        self._carteira = carteira
        self._mercado = dados_mercado

    def __call__(self, lead: Lead) -> list[str]:
        assunto = assunto_pendente(lead)
        try:
            if assunto == "captacao":
                return self._captacao(lead)
            if assunto == "investimento":
                return self._investimento(lead)
            if assunto in ("compra", "aluguel"):
                return self._busca(lead, assunto)
        except Exception:  # noqa: BLE001 — sem argumentos, o follow-up segue genérico
            return []
        return []

    def _compativeis(self, lead: Lead):
        return [im for im in self._imoveis.listar_todos() if self._match.compativel(lead, im)[0] > 0]

    def _busca(self, lead: Lead, assunto: str) -> list[str]:
        imoveis = self._compativeis(lead)
        if not imoveis:
            return ["entram imóveis novos na base com frequência e posso avisar assim que surgir um que combine"]
        melhor = sorted(imoveis, key=lambda im: (-self._match.compativel(lead, im)[0], im.preco))[0]
        valor = formatar_moeda(melhor.preco) + ("/mês" if melhor.tipo_negocio == "aluguel" else "")
        args = [f"{len(imoveis)} imóvel(is) disponível(is) combinam com o que o cliente procura",
                f"destaque: {melhor.tipo_imovel} de {melhor.quartos} quarto(s) em {melhor.bairro} por {valor}"]
        novos = [im for im in imoveis if im.cadastrado_em and im.id not in lead.imoveis_avisados]
        if novos:
            args.append(f"{len(novos)} deles entraram recentemente na base")
        return args

    def _investimento(self, lead: Lead) -> list[str]:
        indicadores = self._mercado.obter_indicadores() if self._mercado else None
        kwargs = {"indicadores": indicadores} if indicadores else {}
        candidatos = [im for im in self._compativeis(lead) if im.tipo_negocio == "venda"]
        if not candidatos:
            return ["há imóveis com bom potencial de renda na base e um especialista em investimentos pode montar opções"]
        analises = [(analisar_investimento(im.preco, None, im.metragem, im.bairro, **kwargs), im) for im in candidatos]
        analise, im = max(analises, key=lambda x: x[0].rentabilidade_aa)
        args = [f"melhor opção dentro do orçamento: {im.tipo_imovel} em {im.bairro} por {formatar_moeda(im.preco)}, "
                f"aluguel estimado de {formatar_moeda(analise.aluguel_estimado)}/mês "
                f"(~{formatar_percentual(analise.rentabilidade_aa)} ao ano)"]
        poupanca = analise.indicadores.poupanca_aa
        if analise.rentabilidade_aa > poupanca:
            args.append(f"isso rende mais que a poupança (~{formatar_percentual(poupanca)} ao ano), "
                        "e o imóvel ainda pode valorizar")
        if analise.valorizacao_12m:
            args.append(f"valorização dos imóveis na região em 12 meses: {formatar_percentual(analise.valorizacao_12m)}")
        return args

    def _captacao(self, lead: Lead) -> list[str]:
        c = lead.captacao
        bairro = c.get("bairro")
        args = []
        if bairro:
            zona = (self._carteira._mapa.zona_de(bairro) or "").lower()  # noqa: SLF001
            procurando = [
                outro for outro in self._leads.listar_todos()
                if outro.id != lead.id and outro.perfil.intencao.value in ("compra", "aluguel", "investimento")
                and zona and zona in {z.lower() for z in self._carteira.zonas_do_lead(outro)}
            ]
            if procurando:
                args.append(f"{len(procurando)} cliente(s) nossos procuram imóvel na região de {bairro} agora")
        if c.get("estimativa"):
            args.append(f"estimativa preliminar do imóvel: {c['estimativa']}")
        args.append("a avaliação com o corretor é gratuita e sem compromisso; faltam só alguns dados do cadastro")
        return args
