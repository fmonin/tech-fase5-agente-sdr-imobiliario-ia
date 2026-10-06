"""Painel da Imobiliária (visão do gestor).

A "Área do Corretor" é o dia a dia de cada corretor; este painel mostra a
operação inteira e responde "como estamos e onde perdemos negócio?":

1. Indicadores (KPIs) do período: leads novos, qualificação, agendamento,
   fechamento, valor fechado, tempo até a 1ª resposta e até o agendamento.
2. Funil completo da operação.
3. Ranking e carga dos corretores.
4. Demanda × oferta: o que os clientes procuram e onde falta imóvel
   (orienta a captação) + motivos de perda somados.
5. "Atenção agora": lista de ações (leads quentes sem visita, leads que
   sumiram depois dos follow-ups, visitas sem resultado, feedback negativo).
6. Desempenho do próprio agente de IA (Sr. Agim).
7. Tabela de leads com nome, corretor responsável e etapa do funil.

Tudo calculado em Python a partir das bases que já existem (leads, agenda,
imóveis, eventos) — sem LLM.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Optional

from src.agents.followup_agent import MAX_FOLLOWUPS, tem_agendamento
from src.domain.entities import Agendamento, Corretor, Lead, RemetenteMensagem
from src.domain.feedback_visita import classificar_feedback
from src.services.carteira_corretor_service import AREA_INVESTIMENTOS, termos_regiao

ETAPAS = ["Em conversa", "Qualificado", "Visita agendada", "Visita realizada", "Proposta", "Negócio fechado"]

_TEMAS = {
    "Fotos do imóvel": r"\b(foto|fotos|imagem|imagens)\b",
    "Preço / valores": r"\b(preco|valor|quanto custa|r\$|mil)\b",
    "Condomínio / IPTU": r"\b(condominio|iptu)\b",
    "Financiamento / FGTS": r"\b(financ\w*|fgts|entrada|parcela)\b",
    "Visita / agendamento": r"\b(visita\w*|agend\w*|marcar|horario)\b",
    "Localização / bairro": r"\b(bairro|perto|proximo|metro|regiao|zona)\b",
    "Retorno do investimento": r"\b(retorno|rentabilidade|render|rendimento)\b",
    "Documentação": r"\b(document\w*|contrato|escritura|fiador)\b",
}


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def inicio_do_lead(lead: Lead) -> datetime:
    return lead.historico[0].criado_em if lead.historico else lead.ultima_interacao_em


def qualificado(lead: Lead) -> bool:
    p = lead.perfil
    return p.dados_essenciais_completos() or p.temperatura.value in ("morno", "quente") or bool(lead.agendamentos)


@dataclass
class ItemAtencao:
    tipo: str
    cliente: str
    detalhe: str
    corretor: str
    prioridade: int  # 1 = mais urgente


@dataclass
class LinhaCorretor:
    corretor: Corretor
    carteira: int
    sem_visita: int
    visitas: int
    realizadas: int
    fechados: int
    taxa_fechamento: Optional[float]
    valor_fechado: float
    cancelamentos: int
    sem_resultado: int


@dataclass
class LinhaDemanda:
    regiao: str
    intencao: str
    clientes: int
    clientes_sem_opcao: int
    imoveis_compativeis: int
    faixa_media: Optional[float]


@dataclass
class PainelGestor:
    periodo_dias: Optional[int]
    leads_novos: int
    taxa_qualificacao: Optional[float]
    taxa_agendamento: Optional[float]
    taxa_fechamento: Optional[float]
    valor_fechado: float
    tempo_primeira_resposta_s: Optional[float]
    tempo_ate_agendamento_h: Optional[float]
    funil: list[tuple[str, int]]
    corretores: list[LinhaCorretor]
    demanda: list[LinhaDemanda]
    motivos_perda: dict[str, int]
    atencao: list[ItemAtencao]
    ia: dict
    leads: list[dict] = field(default_factory=list)
    captacoes: int = 0


class PainelGestorService:
    def __init__(self, lead_repository, agenda_repository, corretor_repository, repositorio_imoveis,
                 carteira, match, desempenho, evento_store=None, horas_lead_quente: float = 2) -> None:
        self._leads = lead_repository
        self._agenda = agenda_repository
        self._corretores = corretor_repository
        self._imoveis = repositorio_imoveis
        self._carteira = carteira
        self._match = match
        self._desempenho = desempenho
        self._eventos = evento_store
        self._horas_lead_quente = horas_lead_quente

    # ------------------------------------------------------------------ geral
    def calcular(self, periodo_dias: Optional[int] = None) -> PainelGestor:
        agora = datetime.utcnow()
        desde = agora - timedelta(days=periodo_dias) if periodo_dias else None
        todos_leads = [l for l in self._leads.listar_todos() if l.historico]
        leads = [l for l in todos_leads if desde is None or inicio_do_lead(l) >= desde]
        corretores = self._corretores.listar_todos()
        agendamentos = self._todos_agendamentos(corretores)
        ag_por_lead: dict[str, list[Agendamento]] = defaultdict(list)
        for a in agendamentos:
            ag_por_lead[a.lead_id].append(a)
            if a.cliente_cpf:
                ag_por_lead["cpf:" + a.cliente_cpf].append(a)
        ids = {l.id for l in leads}

        def do_lead(lead: Lead) -> list[Agendamento]:
            vistos = {a.id: a for a in ag_por_lead.get(lead.id, []) + ag_por_lead.get("cpf:" + (lead.cpf or ""), [])}
            return [a for a in vistos.values() if a.tipo != "avaliacao"
                    and (a.status == "confirmado" or a.cancelado_por or a.resultado_visita)]

        etapas = {l.id: self._etapa(l, do_lead(l)) for l in leads}
        n = len(leads)
        n_qualificados = sum(1 for l in leads if etapas[l.id] != "Em conversa")
        n_agendados = sum(1 for l in leads if ETAPAS.index(etapas[l.id]) >= 2)
        ags_periodo = [a for l in leads for a in do_lead(l)]
        realizadas = [a for a in ags_periodo if a.resultado_visita and a.resultado_visita != "nao_compareceu"]
        propostas = [a for a in ags_periodo if a.resultado_visita in ("proposta", "fechado")]
        fechados = [a for a in ags_periodo if a.resultado_visita == "fechado"]

        funil = [
            ("Leads", n),
            ("Qualificados", n_qualificados),
            ("Com visita agendada", n_agendados),
            ("Visitas realizadas", len(realizadas)),
            ("Propostas", len(propostas)),
            ("Negócios fechados", len(fechados)),
        ]
        responsavel = {l.id: self._corretor_responsavel(l, do_lead(l), corretores) for l in todos_leads}
        return PainelGestor(
            periodo_dias=periodo_dias,
            leads_novos=n,
            taxa_qualificacao=_taxa(n_qualificados, n),
            taxa_agendamento=_taxa(n_agendados, n_qualificados),
            taxa_fechamento=_taxa(len(fechados), len(realizadas)),
            valor_fechado=sum(a.valor_negociado or 0 for a in fechados),
            tempo_primeira_resposta_s=_mediana([t for l in leads if (t := self._primeira_resposta(l)) is not None]),
            tempo_ate_agendamento_h=_mediana([t for l in leads if (t := self._ate_agendamento(l, do_lead(l))) is not None]),
            funil=funil,
            corretores=self._ranking(corretores),
            demanda=self._demanda(leads),
            motivos_perda=self._motivos(corretores),
            atencao=self._atencao(todos_leads, agendamentos, responsavel, agora),
            ia=self._metricas_ia(leads, ids),
            captacoes=sum(1 for a in agendamentos if a.tipo == "avaliacao" and a.status != "cancelado"
                          and (desde is None or a.criado_em >= desde)),
            leads=[
                {
                    "Cliente": l.nome or f"Visitante {l.id[:6]}",
                    "Canal": l.canal,
                    "Intenção": l.perfil.intencao.value,
                    "Temperatura": l.perfil.temperatura.value,
                    "Região": l.perfil.regiao_interesse or "—",
                    "Etapa": etapas[l.id],
                    "Corretor": responsavel.get(l.id) or "—",
                    "Última interação": l.ultima_interacao_em,
                }
                for l in sorted(leads, key=lambda x: x.ultima_interacao_em, reverse=True)
            ],
        )

    def _todos_agendamentos(self, corretores: list[Corretor]) -> list[Agendamento]:
        vistos: dict[str, Agendamento] = {}
        for c in corretores:
            for a in self._agenda.listar_por_corretor(c.id):
                vistos[a.id] = a
        return list(vistos.values())

    @staticmethod
    def _etapa(lead: Lead, ags: list[Agendamento]) -> str:
        resultados = {a.resultado_visita for a in ags}
        if "fechado" in resultados:
            return "Negócio fechado"
        if "proposta" in resultados:
            return "Proposta"
        if resultados - {None, "nao_compareceu"}:
            return "Visita realizada"
        if any(a.status == "confirmado" for a in ags) or tem_agendamento(lead):
            return "Visita agendada"
        return "Qualificado" if qualificado(lead) else "Em conversa"

    def _corretor_responsavel(self, lead: Lead, ags: list[Agendamento], corretores: list[Corretor]) -> Optional[str]:
        if ags:
            return max(ags, key=lambda a: a.criado_em).corretor_nome
        if lead.perfil.intencao.value == "investimento":
            return next((c.nome for c in corretores if AREA_INVESTIMENTOS in c.zonas_atuacao), None)
        zonas = {z.lower() for z in self._carteira.zonas_do_lead(lead)}
        nomes = [c.nome for c in corretores if zonas & {z.lower() for z in c.zonas_atuacao}]
        return " / ".join(nomes) if nomes else None

    @staticmethod
    def _primeira_resposta(lead: Lead) -> Optional[float]:
        primeira_do_lead = next((m for m in lead.historico if m.remetente == RemetenteMensagem.LEAD), None)
        if not primeira_do_lead:
            return None
        resposta = next((m for m in lead.historico if m.remetente == RemetenteMensagem.AGENTE
                         and m.criado_em >= primeira_do_lead.criado_em), None)
        return (resposta.criado_em - primeira_do_lead.criado_em).total_seconds() if resposta else None

    @staticmethod
    def _ate_agendamento(lead: Lead, ags: list[Agendamento]) -> Optional[float]:
        if not ags:
            return None
        primeiro = min(a.criado_em for a in ags)
        horas = (primeiro - inicio_do_lead(lead)).total_seconds() / 3600
        return horas if horas >= 0 else None

    # ------------------------------------------------------------------ 3
    def _ranking(self, corretores: list[Corretor]) -> list[LinhaCorretor]:
        """Visão acumulada de cada corretor (todo o período)."""
        linhas = []
        for c in corretores:
            d = self._desempenho.calcular(c)
            sem_visita = len(self._carteira.clientes_sem_visita(c))
            linhas.append(LinhaCorretor(
                corretor=c,
                carteira=d.leads_da_area,
                sem_visita=sem_visita,
                visitas=d.visitas_agendadas,
                realizadas=d.visitas_realizadas,
                fechados=d.fechados,
                taxa_fechamento=d.taxa_fechamento,
                valor_fechado=d.valor_fechado,
                cancelamentos=d.cancelados_cliente + d.cancelados_corretor,
                sem_resultado=d.sem_resultado,
            ))
        return sorted(linhas, key=lambda l: (-l.valor_fechado, -l.fechados, -l.realizadas, l.corretor.nome))

    # ------------------------------------------------------------------ 4
    def _demanda(self, leads: list[Lead]) -> list[LinhaDemanda]:
        imoveis = self._imoveis.listar_todos()
        grupos: dict[tuple[str, str], list[Lead]] = defaultdict(list)
        for l in leads:
            intencao = l.perfil.intencao.value
            if intencao == "indefinida" or not l.perfil.regiao_interesse:
                continue
            termos = termos_regiao(l.perfil.regiao_interesse)
            mapa = self._carteira._mapa  # noqa: SLF001 (mesma camada de serviço)
            bairros = [mapa.nome_oficial(t) for t in termos if mapa.eh_bairro(t)]
            regiao = bairros[0] if bairros else (termos[0].title() if termos else "—")
            grupos[(regiao, intencao)].append(l)
        linhas = []
        for (regiao, intencao), membros in grupos.items():
            compativeis_por_lead = [
                {im.id for im in imoveis if self._match.compativel(l, im)[0] >= (3 if self._carteira._mapa.eh_bairro(regiao) else 1)}  # noqa: SLF001
                for l in membros
            ]
            faixas = [l.perfil.ticket_investimento if intencao == "investimento" else l.perfil.faixa_preco_max
                      for l in membros]
            faixas = [f for f in faixas if f]
            linhas.append(LinhaDemanda(
                regiao=regiao,
                intencao=intencao,
                clientes=len(membros),
                clientes_sem_opcao=sum(1 for c in compativeis_por_lead if not c),
                imoveis_compativeis=len(set().union(*compativeis_por_lead)) if compativeis_por_lead else 0,
                faixa_media=sum(faixas) / len(faixas) if faixas else None,
            ))
        return sorted(linhas, key=lambda d: (-d.clientes_sem_opcao, -d.clientes, d.regiao))

    def _motivos(self, corretores: list[Corretor]) -> dict[str, int]:
        total: Counter = Counter()
        for c in corretores:
            total.update(self._desempenho.calcular(c).motivos_perda)
        return dict(total.most_common())

    # ------------------------------------------------------------------ 5
    def _atencao(self, leads, agendamentos, responsavel, agora) -> list[ItemAtencao]:
        itens: list[ItemAtencao] = []
        for l in leads:
            if not l.cliente_identificado:
                continue  # sem nome/CPF o gestor não tem como agir
            nome = l.nome or f"Visitante {l.id[:6]}"
            horas = (agora - l.ultima_interacao_em).total_seconds() / 3600
            if l.perfil.temperatura.value == "quente" and not tem_agendamento(l) and horas >= self._horas_lead_quente:
                itens.append(ItemAtencao("🔥 Lead quente sem visita", nome,
                                         f"última conversa há {_tempo(horas)}", responsavel.get(l.id) or "—", 1))
            if l.followups_enviados >= MAX_FOLLOWUPS and l.aguardando_resposta_desde:
                itens.append(ItemAtencao("📵 Parou de responder (após follow-ups)", nome,
                                         f"{l.followups_enviados} follow-ups sem resposta — vale um contato humano",
                                         responsavel.get(l.id) or "—", 2))
        agora_local = datetime.now()
        for a in agendamentos:
            cliente = a.cliente_nome or "Cliente"
            if a.status == "confirmado" and not a.resultado_visita and a.data_hora and a.data_hora < agora_local:
                itens.append(ItemAtencao("📝 Visita sem resultado registrado", cliente,
                                         f"{a.quando_formatado()}{' — ' + a.imovel_titulo if a.imovel_titulo else ''}",
                                         a.corretor_nome or "—", 2))
            if a.feedback_cliente and classificar_feedback(a.feedback_cliente)[0] == "negativo" \
                    and a.resultado_visita in (None, "gostou", "proposta"):
                itens.append(ItemAtencao("👎 Feedback negativo na pós-visita", cliente,
                                         f"“{a.feedback_cliente}”", a.corretor_nome or "—", 1))
        return sorted(itens, key=lambda i: (i.prioridade, i.tipo, i.cliente))

    # ------------------------------------------------------------------ 6
    def _metricas_ia(self, leads: list[Lead], ids: set[str]) -> dict:
        msgs_cliente = [sum(1 for m in l.historico if m.remetente == RemetenteMensagem.LEAD) for l in leads]
        ate_agendar = []
        for l in leads:
            if l.agendamentos:
                primeiro = min(a.criado_em for a in l.agendamentos)
                ate_agendar.append(sum(1 for m in l.historico
                                       if m.remetente == RemetenteMensagem.LEAD and m.criado_em <= primeiro))
        disparos, responderam = 0, 0
        if self._eventos is not None:
            por_id = {l.id: l for l in leads}
            for e in self._eventos.listar_eventos(5000):
                if e["nome"] != "followup_disparado" or e["dados"].get("lead_id") not in ids:
                    continue
                disparos += 1
                quando = datetime.fromisoformat(e["criado_em"])
                lead = por_id[e["dados"]["lead_id"]]
                if any(m.remetente == RemetenteMensagem.LEAD and m.criado_em > quando for m in lead.historico):
                    responderam += 1
        temas: Counter = Counter()
        for l in leads:
            for m in l.historico:
                if m.remetente != RemetenteMensagem.LEAD:
                    continue
                t = _normalizar(m.conteudo)
                for tema, padrao in _TEMAS.items():
                    if re.search(padrao, t):
                        temas[tema] += 1
        return {
            "conversas": len(leads),
            "msgs_por_conversa": (sum(msgs_cliente) / len(msgs_cliente)) if msgs_cliente else None,
            "msgs_ate_agendar": (sum(ate_agendar) / len(ate_agendar)) if ate_agendar else None,
            "followups": disparos,
            "taxa_reengajamento": _taxa(responderam, disparos),
            "canais": dict(Counter(l.canal for l in leads)),
            "identificados": sum(1 for l in leads if l.cliente_identificado),
            "temas": dict(temas.most_common(8)),
            "notas": [n for l in leads for n in l.notas_atendimento],
            "encerrados": sum(1 for l in leads if l.atendimento_encerrado),
        }


def _taxa(parte: int, todo: int) -> Optional[float]:
    return parte / todo if todo else None


def _mediana(valores: list[float]) -> Optional[float]:
    return median(valores) if valores else None


def _tempo(horas: float) -> str:
    if horas < 1:
        return f"{horas * 60:.0f} min"
    if horas < 48:
        return f"{horas:.0f} h"
    return f"{horas / 24:.0f} dias"
