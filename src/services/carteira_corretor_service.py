"""Carteira do corretor: consultas prontas para a "Área do Corretor".

1. **Clientes sem visita agendada** — leads que procuraram imóveis na área
   de atuação do corretor (zona do bairro/zona pedidos ou do imóvel de
   interesse; para o especialista em Investimentos, os investidores) e ainda
   não têm visita/reunião marcada. É a lista de "quem eu devo ligar hoje".

2. **Sugestões para clientes agendados** — para cada agendamento ativo do
   corretor, além do imóvel escolhido pelo cliente, outros imóveis parecidos
   (mesmo bairro → vizinhos → mesma zona; preço e quartos próximos; para
   investidor, os de maior retorno) para o corretor levar na visita e
   aumentar a chance de fechar negócio.

Tudo é calculado em Python (regras determinísticas, sem LLM): a tela é uma
consulta de dados e precisa ser rápida e confiável.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.agents.apresentacao_imoveis import resumo_imovel
from src.agents.contexto_conversa import descrever_interesse
from src.agents.followup_agent import tem_agendamento
from src.agents.foco_imovel import ids_mostrados
from src.domain.entities import Agendamento, Corretor, Imovel, Lead, RemetenteMensagem
from src.domain.interfaces import IAgendaRepository, ILeadRepository, IMarketDataRepository, IPropertyRepository
from src.domain.investimento import analisar_investimento, formatar_moeda
from src.domain.localizacao import MapaBairros, normalizar_nome as _normalizar_nome

AREA_INVESTIMENTOS = "Investimentos"
_ORDEM_TEMPERATURA = {"quente": 0, "morno": 1, "frio": 2}


@dataclass
class ClienteSemVisita:
    lead: Lead
    interesse: str
    situacao: str
    zonas: list[str]
    imovel_interesse: Optional[Imovel] = None


@dataclass
class ImovelSugerido:
    imovel: Imovel
    resumo: str
    motivo: str
    ja_mostrado_ao_cliente: bool


@dataclass
class SugestoesDoAgendamento:
    agendamento: Agendamento
    lead: Optional[Lead]
    intencao: str
    imovel_escolhido: Optional[Imovel]
    resumo_escolhido: str
    sugestoes: list[ImovelSugerido] = field(default_factory=list)


def termos_regiao(regiao: Optional[str]) -> list[str]:
    """'Zona Leste, Mooca' -> ['Zona Leste', 'Mooca']."""
    if not regiao:
        return []
    return [t.strip() for t in re.split(r",|/|;|\be\b|\bou\b", regiao) if t.strip()]


class CarteiraCorretorService:
    def __init__(
        self,
        lead_repository: ILeadRepository,
        repositorio_imoveis: IPropertyRepository,
        agenda_repository: IAgendaRepository,
        mapa_bairros: MapaBairros,
        dados_mercado: Optional[IMarketDataRepository] = None,
    ) -> None:
        self._leads = lead_repository
        self._imoveis = repositorio_imoveis
        self._agenda = agenda_repository
        self._mapa = mapa_bairros
        self._mercado = dados_mercado

    # ------------------------------------------------------------------ 1
    def clientes_sem_visita(self, corretor: Corretor) -> list[ClienteSemVisita]:
        por_id = self._imoveis_por_id()
        especialista = AREA_INVESTIMENTOS in corretor.zonas_atuacao
        zonas_corretor = {z.lower() for z in corretor.zonas_atuacao}
        resultado: list[ClienteSemVisita] = []
        for lead in self._leads.listar_todos():
            perfil = lead.perfil
            intencao = perfil.intencao.value
            if not lead.cliente_identificado or intencao == "indefinida" or tem_agendamento(lead):
                continue
            imovel = por_id.get(perfil.imovel_interesse_id or "")
            zonas = self._zonas_do_lead(lead, imovel)
            if especialista:
                if intencao != "investimento":
                    continue
            elif intencao == "investimento" or not zonas_corretor & {z.lower() for z in zonas}:
                continue
            resultado.append(ClienteSemVisita(
                lead=lead,
                interesse=descrever_interesse({
                    "intencao": intencao,
                    "regiao_interesse": perfil.regiao_interesse,
                    "quartos_desejados": perfil.quartos_desejados,
                    "faixa_preco_max": perfil.faixa_preco_max,
                    "ticket_investimento": perfil.ticket_investimento,
                    "expectativa_retorno": perfil.expectativa_retorno,
                }),
                situacao=self._situacao(lead),
                zonas=zonas,
                imovel_interesse=imovel,
            ))
        resultado.sort(key=lambda c: (_ORDEM_TEMPERATURA.get(c.lead.perfil.temperatura.value, 3),
                                      -c.lead.ultima_interacao_em.timestamp()))
        return resultado

    def zonas_do_lead(self, lead: Lead, imovel: Optional[Imovel] = None) -> list[str]:
        return self._zonas_do_lead(lead, imovel)

    def _zonas_do_lead(self, lead: Lead, imovel: Optional[Imovel]) -> list[str]:
        zonas: list[str] = []
        for termo in termos_regiao(lead.perfil.regiao_interesse):
            if self._mapa.eh_bairro(termo):
                zonas.append(self._mapa.zona_de(termo) or "")
            elif termo.lower().startswith("zona"):
                zonas.append(termo.title())
            elif termo.lower() in ("centro", "central"):
                zonas.append("Zona Central")
        if imovel:
            zonas.append(imovel.zona)
        return [z for z in dict.fromkeys(zonas) if z]

    @staticmethod
    def _situacao(lead: Lead) -> str:
        if any(a.status == "sugerido" for a in lead.agendamentos):
            return "Horário proposto, aguardando confirmação do cliente"
        if any(a.status == "cancelado" for a in lead.agendamentos):
            return "Agendamento cancelado — vale retomar o contato"
        if lead.aguardando_resposta_desde:
            return "Parou de responder"
        return "Ainda não agendou"

    # ------------------------------------------------------------------ imóveis da área
    def imoveis_da_area(
        self,
        corretor: Corretor,
        negocio: Optional[str] = None,  # "venda" | "aluguel"
        bairro: Optional[str] = None,
    ) -> list[Imovel]:
        """Todos os imóveis da base na área do corretor (zonas de atuação); para
        o especialista em Investimentos, os marcados como bons para investir."""
        especialista = AREA_INVESTIMENTOS in corretor.zonas_atuacao
        zonas = {z.lower() for z in corretor.zonas_atuacao}
        itens = [
            im for im in self._imoveis.listar_todos()
            if (im.finalidade_investimento if especialista else im.zona.lower() in zonas)
            and (negocio is None or im.tipo_negocio == negocio)
            and (bairro is None or im.bairro.lower() == bairro.lower())
        ]
        return sorted(itens, key=lambda im: (im.tipo_negocio, im.bairro, im.preco))

    def bairros_no_texto(self, texto: str) -> Optional[str]:
        t = f" {re.sub(r'[,.;!?]', ' ', _normalizar_nome(texto))} "
        achados = [nome for chave, nome in self._mapa.nomes.items() if f" {chave} " in t]
        return max(achados, key=len) if achados else None

    # ------------------------------------------------------------------ 2
    def sugestoes_para_agendados(self, corretor: Corretor, limite: int = 3) -> list[SugestoesDoAgendamento]:
        por_id = self._imoveis_por_id()
        indicadores = self._mercado.obter_indicadores() if self._mercado else None
        ativos = sorted(
            (a for a in self._agenda.listar_por_corretor(corretor.id)
             if a.status != "cancelado" and a.tipo != "avaliacao"),
            key=lambda a: a.data_hora or datetime.max,
        )
        resultado: list[SugestoesDoAgendamento] = []
        for ag in ativos:
            lead = self._leads.buscar_por_id(ag.lead_id) if ag.lead_id else None
            perfil = lead.perfil if lead else None
            escolhido = por_id.get(ag.imovel_id or "") or (por_id.get(perfil.imovel_interesse_id or "") if perfil else None)
            intencao = perfil.intencao.value if perfil else "indefinida"
            if escolhido and not (intencao == "investimento" and escolhido.tipo_negocio == "venda"):
                # O imóvel da visita manda (o lead pode ter mudado de busca depois).
                intencao = "aluguel" if escolhido.tipo_negocio == "aluguel" else "compra"
            mostrados = self._ids_mostrados(lead)
            sugestoes = self._sugerir(lead, intencao, escolhido, mostrados, indicadores)[:limite]
            resultado.append(SugestoesDoAgendamento(
                agendamento=ag,
                lead=lead,
                intencao=intencao,
                imovel_escolhido=escolhido,
                resumo_escolhido=resumo_imovel(escolhido, intencao, indicadores) if escolhido else "",
                sugestoes=sugestoes,
            ))
        return resultado

    def _sugerir(self, lead, intencao, escolhido, mostrados, indicadores) -> list[ImovelSugerido]:
        perfil = lead.perfil if lead else None
        negocio = "aluguel" if intencao == "aluguel" else "venda"
        if escolhido:
            negocio = escolhido.tipo_negocio
        bairro_ref = escolhido.bairro if escolhido else None
        if not bairro_ref and perfil:
            bairro_ref = next((t for t in termos_regiao(perfil.regiao_interesse) if self._mapa.eh_bairro(t)), None)
        zonas_ref = {z.lower() for z in self._zonas_do_lead(lead, escolhido)} if lead else (
            {escolhido.zona.lower()} if escolhido else set())
        vizinhos = {v.lower() for v in self._mapa.vizinhos_de(bairro_ref)} if bairro_ref else set()

        if intencao == "investimento":
            teto = (perfil.ticket_investimento if perfil and perfil.ticket_investimento else None) or (
                escolhido.preco if escolhido else None)
            preco_min, preco_max = 0.0, (teto * 1.15 if teto else float("inf"))
        else:
            ref = escolhido.preco if escolhido else (perfil.faixa_preco_max if perfil else None)
            preco_min, preco_max = (ref * 0.75, ref * 1.25) if ref else (0.0, float("inf"))
        quartos_min = escolhido.quartos if escolhido else (perfil.quartos_desejados or 0 if perfil else 0)

        kwargs = {"indicadores": indicadores} if indicadores else {}
        candidatos = []
        for im in self._imoveis.listar_todos():
            if im.tipo_negocio != negocio or (escolhido and im.id == escolhido.id):
                continue
            if not (preco_min <= im.preco <= preco_max) or im.quartos < max(0, quartos_min - 1):
                continue
            if bairro_ref and im.bairro.lower() == bairro_ref.lower():
                local, onde = 0, f"mesmo bairro ({im.bairro})"
            elif im.bairro.lower() in vizinhos:
                local, onde = 1, f"bairro vizinho ({im.bairro})"
            elif im.zona.lower() in zonas_ref:
                local, onde = 2, f"mesma região ({im.zona})"
            else:
                continue
            partes = [onde]
            if intencao == "investimento":
                rent = analisar_investimento(im.preco, None, im.metragem, im.bairro, **kwargs).rentabilidade_aa
                chave = (im.id in mostrados, -rent, local)
                partes.append(f"retorno estimado {rent * 100:.1f}% a.a.".replace(".", ","))
            else:
                ref = escolhido.preco if escolhido else (perfil.faixa_preco_max if perfil and perfil.faixa_preco_max else im.preco)
                diferenca = (im.preco - ref) / ref if ref else 0.0
                chave = (im.id in mostrados, local, abs(diferenca))
                if abs(diferenca) >= 0.01:
                    partes.append(f"{abs(diferenca) * 100:.0f}% {'mais caro' if diferenca > 0 else 'mais barato'}"
                                  f" ({formatar_moeda(im.preco)})")
                else:
                    partes.append("mesmo preço")
                if escolhido and im.quartos > escolhido.quartos:
                    partes.append(f"+{im.quartos - escolhido.quartos} quarto(s)")
                if escolhido and im.vagas > escolhido.vagas:
                    partes.append("mais vagas")
            candidatos.append((chave, ImovelSugerido(
                imovel=im,
                resumo=resumo_imovel(im, intencao, indicadores),
                motivo=" · ".join(partes),
                ja_mostrado_ao_cliente=im.id in mostrados,
            )))
        return [s for _, s in sorted(candidatos, key=lambda c: c[0])]

    # ------------------------------------------------------------------ util
    def _imoveis_por_id(self) -> dict[str, Imovel]:
        return {im.id: im for im in self._imoveis.listar_todos()}

    @staticmethod
    def _ids_mostrados(lead: Optional[Lead]) -> set[str]:
        if not lead:
            return set()
        return {i for m in lead.historico if m.remetente == RemetenteMensagem.AGENTE for i in ids_mostrados(m.conteudo)}
