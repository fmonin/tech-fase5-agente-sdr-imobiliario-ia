"""Entidades de domínio.

Entidades são os "substantivos" do negócio: Lead, Imóvel, Mensagem,
Agendamento, Resumo. Elas não sabem nada sobre banco de dados, LangGraph,
Streamlit ou Azure — são apenas estruturas de dados + regras simples.
Isso é o que, em SOLID, ajuda o **D**ependency Inversion Principle: as
camadas de cima (agentes, serviços) dependem destas entidades simples, e as
camadas de baixo (infraestrutura) também dependem delas — ninguém depende
de detalhes de implementação de outra camada.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4


class IntencaoLead(str, Enum):
    """Intenção principal identificada na conversa."""

    COMPRA = "compra"
    ALUGUEL = "aluguel"
    INVESTIMENTO = "investimento"
    INDEFINIDA = "indefinida"


class TemperaturaLead(str, Enum):
    """Qualificação do lead (o quão pronto ele está para avançar)."""

    FRIO = "frio"
    MORNO = "morno"
    QUENTE = "quente"


class RemetenteMensagem(str, Enum):
    LEAD = "lead"
    AGENTE = "agente"
    SISTEMA = "sistema"


@dataclass
class Imovel:
    """Um imóvel da base simulada da imobiliária."""

    id: str
    titulo: str
    tipo_negocio: str  # "venda" | "aluguel"
    finalidade_investimento: bool
    zona: str
    bairro: str
    preco: float
    quartos: int
    metragem: float
    descricao: str
    # Caminhos das fotos (relativos à raiz do projeto), ex.: data/imagens/IM012/1_fachada.jpg
    fotos: list[str] = field(default_factory=list)
    tipo_imovel: str = "Apartamento"  # Apartamento | Casa | Studio | Kitnet | Cobertura | Sala comercial
    suites: int = 0
    vagas: int = 0
    condominio: Optional[float] = None  # R$/mês (None = sem condomínio)
    # Imóveis cadastrados pelo corretor na "Área do Corretor" (match com clientes).
    cadastrado_em: Optional[str] = None  # ISO 8601
    cadastrado_por: Optional[str] = None  # id do corretor

    def resumo(self) -> str:
        # Formato brasileiro (R$ 480.000,00). Antes saía "R$ 480.000.00".
        from src.domain.investimento import formatar_moeda

        preco_fmt = formatar_moeda(self.preco)
        return (
            f"{self.titulo} — {self.bairro}/{self.zona} — {self.quartos} quarto(s) — "
            f"{self.metragem:.0f}m² — {preco_fmt}"
        )


@dataclass
class Mensagem:
    remetente: RemetenteMensagem
    conteudo: str
    criado_em: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PerfilLead:
    """Informações coletadas ao longo da conversa (vai sendo preenchido aos poucos)."""

    intencao: IntencaoLead = IntencaoLead.INDEFINIDA
    faixa_preco_min: Optional[float] = None
    faixa_preco_max: Optional[float] = None
    quartos_desejados: Optional[int] = None
    regiao_interesse: Optional[str] = None
    urgencia: Optional[str] = None  # ex.: "imediata", "1-3 meses", "sem pressa"
    ticket_investimento: Optional[float] = None
    expectativa_retorno: Optional[str] = None
    temperatura: TemperaturaLead = TemperaturaLead.FRIO
    # Último imóvel em foco (detalhado ou sugerido) — usado para "esse imóvel"
    # e para o agendamento saber qual imóvel/corretor.
    imovel_interesse_id: Optional[str] = None

    def dados_essenciais_completos(self) -> bool:
        """Regra simples de qualificação: sabemos o suficiente para agir?"""
        if self.intencao == IntencaoLead.INVESTIMENTO:
            return self.ticket_investimento is not None and self.expectativa_retorno is not None
        return bool(self.regiao_interesse) and self.quartos_desejados is not None and (
            self.faixa_preco_min is not None or self.faixa_preco_max is not None
        )


@dataclass
class Agendamento:
    """Um agendamento de reunião/visita.

    Além de ficar "pendurado" no histórico do lead (para exibir no chat),
    todo agendamento também é gravado na base de agenda dedicada
    (`IAgendaRepository`), que é o que permite ao cliente OU ao corretor
    consultar os agendamentos depois — ver `SqliteAgendaRepository`.
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    lead_id: str = ""
    quando_sugerido: str = ""
    tipo: str = "reuniao"  # "reuniao" | "visita" | "avaliacao" (captação: imóvel do cliente à venda/locação)
    status: str = "sugerido"  # "sugerido" | "confirmado" | "cancelado"
    confirmado: bool = False
    data_hora: Optional[datetime] = None
    cliente_cpf: Optional[str] = None
    cliente_nome: Optional[str] = None
    corretor_id: Optional[str] = None
    corretor_nome: Optional[str] = None
    imovel_id: Optional[str] = None
    imovel_titulo: Optional[str] = None
    criado_em: datetime = field(default_factory=datetime.utcnow)
    # Cancelamento (ex.: pelo corretor na "Área do Corretor"). O cliente é
    # avisado na próxima vez que se identificar (`cliente_notificado`).
    cancelado_por: Optional[str] = None  # "corretor" | "cliente"
    motivo_cancelamento: Optional[str] = None
    cancelado_em: Optional[datetime] = None
    cliente_notificado: bool = False
    # Remarcação feita pelo corretor (ex.: pelo chat da "Área do Corretor").
    horario_anterior: Optional[str] = None
    motivo_alteracao: Optional[str] = None
    # Cancelamento/remarcação feito pelo CLIENTE: False até o corretor ver o
    # aviso na "Área do Corretor" (mesma ideia de `cliente_notificado`).
    corretor_notificado: bool = True
    # Pós-visita: resultado registrado pelo corretor (funil de vendas) e o
    # retorno do cliente à pergunta automática "o que achou da visita?".
    resultado_visita: Optional[str] = None  # ver RESULTADOS_VISITA
    motivo_resultado: Optional[str] = None  # ex.: "Preço" (quando não gostou)
    observacao_resultado: Optional[str] = None
    resultado_em: Optional[datetime] = None
    pos_visita_enviado: bool = False
    feedback_cliente: Optional[str] = None
    valor_negociado: Optional[float] = None  # valor da proposta / do negócio fechado (R$)
    detalhes: Optional[str] = None  # ficha do imóvel na captação (tipo, quartos, endereço, estimativa...)

    @property
    def tipo_texto(self) -> str:
        """'visita', 'reunião' ou 'avaliação do imóvel' (para mensagens e telas)."""
        return {"visita": "visita", "avaliacao": "avaliação do imóvel"}.get(self.tipo, "reunião")

    def quando_formatado(self) -> str:
        """'dia 05/10 às 15h' (ou o texto sugerido, se não houver data)."""
        d = self.data_hora
        if d is None:
            return self.quando_sugerido
        hora = f"{d:%H}h" if not d.minute else f"{d:%H}h{d:%M}"
        return f"dia {d:%d/%m} às {hora}"


@dataclass
class Corretor:
    """Um corretor humano que assume os leads qualificados pelo agente.

    `zonas_atuacao` é o que permite ao `AgendadorAgent` escolher
    automaticamente o corretor certo para cada agendamento, de acordo com a
    zona do imóvel/interesse do lead.
    """

    id: str
    nome: str
    zonas_atuacao: list[str]
    email: Optional[str] = None
    telefone: Optional[str] = None


@dataclass
class Lead:
    """Representa um cliente/lead em conversa com o agente.

    `cpf` fica `None` até o `IdentificacaoAgent` completar o cadastro do
    cliente (nome + CPF) — antes disso, o grafo multiagente nem chega a
    rodar os agentes de qualificação/imóveis/agendamento, veja
    `src/agents/graph.py`.
    """

    id: str = field(default_factory=lambda: str(uuid4()))
    nome: Optional[str] = None
    cpf: Optional[str] = None
    canal: str = "web"  # "web" | "telegram"
    perfil: PerfilLead = field(default_factory=PerfilLead)
    historico: list[Mensagem] = field(default_factory=list)
    agendamentos: list[Agendamento] = field(default_factory=list)
    ultima_interacao_em: datetime = field(default_factory=datetime.utcnow)
    aguardando_resposta_desde: Optional[datetime] = None
    # Follow-ups enviados desde a última resposta do lead (limita a insistência).
    followups_enviados: int = 0
    # Último resumo inteligente gerado para o corretor (ResumidorAgent).
    resumo_corretor: Optional[str] = None
    # Interesses anteriores quando o lead troca de busca (ex.: compra -> "quero alugar também").
    buscas_anteriores: list[str] = field(default_factory=list)
    # Visitas de que o cliente NÃO gostou e por quê ({imovel_id, motivo, preco,
    # metragem, bairro}): o Sr. Agim usa isso para sugerir opções melhores.
    feedback_visitas: list[dict] = field(default_factory=list)
    # Imóveis novos já avisados a este cliente (match), para não repetir.
    imoveis_avisados: list[str] = field(default_factory=list)
    # Captação: o cliente quer VENDER ou colocar para ALUGAR o próprio imóvel.
    # `captacao` = cadastro em andamento; `captacoes` = cadastros concluídos.
    captacao: dict = field(default_factory=dict)
    captacoes: list[dict] = field(default_factory=list)
    # O cliente encerrou o atendimento: sem follow-up até ele mandar nova mensagem.
    atendimento_encerrado: bool = False
    notas_atendimento: list[int] = field(default_factory=list)  # satisfação 1-5
    # Pediu para não receber mais mensagens ("pare de me mandar mensagem"): nada de follow-up.
    nao_contatar: bool = False

    def registrar_mensagem(self, remetente: RemetenteMensagem, conteudo: str) -> None:
        self.historico.append(Mensagem(remetente=remetente, conteudo=conteudo))
        self.ultima_interacao_em = datetime.utcnow()
        if remetente == RemetenteMensagem.AGENTE:
            self.aguardando_resposta_desde = datetime.utcnow()
        else:
            self.aguardando_resposta_desde = None
            self.followups_enviados = 0

    @property
    def cliente_identificado(self) -> bool:
        """True quando o cadastro do cliente (nome + CPF) já foi concluído."""
        return bool(self.cpf and self.nome)


@dataclass
class ResumoCorretor:
    """Resumo inteligente gerado para o corretor humano assumir o lead."""

    lead_id: str
    texto: str
    intencao: IntencaoLead
    temperatura: TemperaturaLead
    proximos_passos: str
    gerado_em: datetime = field(default_factory=datetime.utcnow)
