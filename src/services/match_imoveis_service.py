"""Match de imóveis novos × clientes.

Quando um imóvel novo entra na base (cadastrado pelo corretor na "Área do
Corretor"), o sistema procura os clientes que procuram exatamente aquilo —
mesma intenção (venda/aluguel/investimento), mesma região (bairro, vizinho
ou zona), dentro do orçamento e com os quartos pedidos — e o corretor pode
avisá-los com um clique: o Sr. Agim manda a mensagem no chat do cliente
(e no Telegram, se ele conversa por lá).

Regras em Python (sem LLM), como a carteira do corretor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from src.agents.apresentacao_imoveis import resumo_imovel
from src.agents.contexto_conversa import descrever_interesse
from src.domain.entities import Corretor, Imovel, Lead, RemetenteMensagem
from src.domain.interfaces import ILeadRepository, IMarketDataRepository, IPropertyRepository
from src.domain.localizacao import MapaBairros
from src.domain.midia import anexar_lista, separar_midia
from src.services.carteira_corretor_service import AREA_INVESTIMENTOS, termos_regiao


@dataclass
class ClienteCompativel:
    lead: Lead
    aderencia: int  # 3 = mesmo bairro, 2 = vizinho, 1 = mesma zona
    motivo: str
    ja_avisado: bool


@dataclass
class ImovelComMatch:
    imovel: Imovel
    clientes: list[ClienteCompativel]


class MatchImoveisService:
    def __init__(
        self,
        repositorio_imoveis: IPropertyRepository,
        lead_repository: ILeadRepository,
        mapa_bairros: MapaBairros,
        dados_mercado: Optional[IMarketDataRepository] = None,
        enviar_telegram: Optional[Callable[[str, str], None]] = None,
        observador=None,
    ) -> None:
        self._imoveis = repositorio_imoveis
        self._leads = lead_repository
        self._mapa = mapa_bairros
        self._mercado = dados_mercado
        self._enviar_telegram = enviar_telegram  # (lead_id, texto) -> envia aos chats do lead
        self._observador = observador

    def bairros_disponiveis(self) -> list[str]:
        return sorted(set(self._mapa.nomes.values()))

    # ------------------------------------------------------------ cadastro
    def cadastrar(self, imovel: Imovel, corretor: Corretor) -> ImovelComMatch:
        imovel.cadastrado_em = datetime.utcnow().isoformat()
        imovel.cadastrado_por = corretor.id
        if not imovel.zona:
            imovel.zona = self._mapa.zona_de(imovel.bairro) or ""
        imovel.bairro = self._mapa.nome_oficial(imovel.bairro)
        self._imoveis.adicionar(imovel)
        if self._observador:
            self._observador.registrar_evento("imovel_cadastrado", {"imovel_id": imovel.id, "corretor_id": corretor.id})
        return ImovelComMatch(imovel, self.clientes_para_imovel(imovel))

    # ------------------------------------------------------------ match
    def compativel(self, lead: Lead, imovel: Imovel) -> tuple[int, str]:
        """(aderência 0-3, onde). 0 = o imóvel não serve para o lead."""
        p = lead.perfil
        intencao = p.intencao.value
        if intencao == "indefinida":
            return 0, ""
        if any(f.get("imovel_id") == imovel.id for f in lead.feedback_visitas):
            return 0, ""  # já visitou e não gostou
        if (intencao == "aluguel") != (imovel.tipo_negocio == "aluguel"):
            return 0, ""
        teto = p.ticket_investimento if intencao == "investimento" else p.faixa_preco_max
        if teto and imovel.preco > teto * 1.10:
            return 0, ""
        if intencao != "investimento" and p.faixa_preco_min and imovel.preco < p.faixa_preco_min * 0.8:
            return 0, ""
        if intencao != "investimento" and p.quartos_desejados and imovel.quartos < p.quartos_desejados:
            return 0, ""
        return self._aderencia(lead, imovel)

    def clientes_para_imovel(self, imovel: Imovel) -> list[ClienteCompativel]:
        resultado: list[ClienteCompativel] = []
        for lead in self._leads.listar_todos():
            p = lead.perfil
            intencao = p.intencao.value
            if not lead.cliente_identificado:
                continue
            aderencia, onde = self.compativel(lead, imovel)
            if not aderencia:
                continue
            resultado.append(ClienteCompativel(
                lead=lead,
                aderencia=aderencia,
                motivo=f"procura {descrever_interesse({'intencao': intencao, 'regiao_interesse': p.regiao_interesse, 'quartos_desejados': p.quartos_desejados, 'faixa_preco_max': p.faixa_preco_max, 'ticket_investimento': p.ticket_investimento})} · {onde}",
                ja_avisado=imovel.id in lead.imoveis_avisados,
            ))
        return sorted(resultado, key=lambda c: (c.ja_avisado, -c.aderencia, -c.lead.ultima_interacao_em.timestamp()))

    def _aderencia(self, lead: Lead, imovel: Imovel) -> tuple[int, str]:
        termos = termos_regiao(lead.perfil.regiao_interesse)
        if not termos:
            return (1, "sem região definida") if lead.perfil.intencao.value == "investimento" else (0, "")
        melhor, onde = 0, ""
        for termo in termos:
            if self._mapa.eh_bairro(termo):
                bairro = self._mapa.nome_oficial(termo)
                if bairro.lower() == imovel.bairro.lower():
                    return 3, f"mesmo bairro ({imovel.bairro})"
                if imovel.bairro.lower() in {v.lower() for v in self._mapa.vizinhos_de(bairro)} and melhor < 2:
                    melhor, onde = 2, f"bairro vizinho de {bairro}"
                elif (self._mapa.zona_de(bairro) or "").lower() == imovel.zona.lower() and melhor < 1:
                    melhor, onde = 1, f"mesma região ({imovel.zona})"
            elif termo.lower().startswith("zona") and termo.title().lower() == imovel.zona.lower() and melhor < 1:
                melhor, onde = 1, f"na região pedida ({imovel.zona})"
        return melhor, onde

    def imoveis_novos_com_match(self, corretor: Corretor, dias: int = 30) -> list[ImovelComMatch]:
        """Imóveis cadastrados nos últimos `dias` na área do corretor (ou por
        ele), cada um com os clientes compatíveis."""
        limite = datetime.utcnow() - timedelta(days=dias)
        especialista = AREA_INVESTIMENTOS in corretor.zonas_atuacao
        zonas = {z.lower() for z in corretor.zonas_atuacao}
        itens = []
        for im in self._imoveis.listar_todos():
            if not im.cadastrado_em or datetime.fromisoformat(im.cadastrado_em) < limite:
                continue
            da_area = im.cadastrado_por == corretor.id or im.zona.lower() in zonas or (
                especialista and im.tipo_negocio == "venda")
            if not da_area:
                continue
            clientes = self.clientes_para_imovel(im)
            if especialista:
                clientes = [c for c in clientes if c.lead.perfil.intencao.value == "investimento"]
            itens.append(ImovelComMatch(im, clientes))
        return sorted(itens, key=lambda i: i.imovel.cadastrado_em or "", reverse=True)

    # ------------------------------------------------------------ aviso
    def avisar_cliente(self, lead_id: str, imovel_id: str, corretor: Corretor) -> Optional[str]:
        lead = self._leads.buscar_por_id(lead_id)
        imovel = next((im for im in self._imoveis.listar_todos() if im.id == imovel_id), None)
        if lead is None or imovel is None:
            return None
        intencao = lead.perfil.intencao.value
        indicadores = self._mercado.obter_indicadores() if self._mercado else None
        primeiro = (lead.nome or "").split(" ")[0]
        texto = (
            f"Oi{', ' + primeiro if primeiro else ''}! 🏡 Acabou de entrar um imóvel que tem tudo a ver com o que "
            f"você procura:\n\n1) {resumo_imovel(imovel, intencao, indicadores)}\n\n"
            "Quer ver as fotos ou agendar uma visita?"
        )
        texto = anexar_lista(texto, [imovel.id])  # "fotos do 1" funciona
        lead.registrar_mensagem(RemetenteMensagem.AGENTE, texto)
        lead.perfil.imovel_interesse_id = imovel.id
        if imovel.id not in lead.imoveis_avisados:
            lead.imoveis_avisados.append(imovel.id)
        self._leads.salvar(lead)
        if self._enviar_telegram:
            try:
                self._enviar_telegram(lead.id, separar_midia(texto)[0])
            except Exception:  # noqa: BLE001 — sem Telegram, o cliente vê no chat web
                pass
        if self._observador:
            self._observador.registrar_evento(
                "match_avisado", {"lead_id": lead.id, "imovel_id": imovel.id, "corretor_id": corretor.id})
        return texto
