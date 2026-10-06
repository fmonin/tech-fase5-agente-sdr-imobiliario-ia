"""Agente de Follow-up.

Responsabilidade única: reengajar leads que pararam de responder — o
"Exemplo 3" do desafio ("Cliente iniciou conversa e não respondeu"):
    • retomar contato automaticamente;
    • manter o contexto da conversa;
    • reengajar o lead.

Diferente dos demais agentes (que reagem a uma mensagem recebida), este é
acionado periodicamente: automaticamente pelo bot do Telegram (a cada
`[followup]` em `config/settings.toml`) ou pelo script `scripts/executar_followup.py`
(que em produção rodaria num agendador — cron, Azure Functions etc.).

Regras para não virar spam:
    • no máximo `MAX_FOLLOWUPS` mensagens seguidas sem resposta do lead
      (o contador zera quando o lead responde);
    • negócio pela metade (compra, aluguel, investimento, venda/locação do
      próprio imóvel) recebe até `MAX_FOLLOWUPS_NEGOCIO_PENDENTE` mensagens com
      ARGUMENTOS reais (`ArgumentosFollowUp`) — mesmo que o cliente tenha
      encerrado a conversa; quem pediu "pare de me mandar mensagens" nunca
      mais recebe (`Lead.nao_contatar`);
    • só reengaja conversas SEM agendamento: quem já tem visita/reunião
      confirmada (ou remarcada pelo corretor) não recebe follow-up. Proposta
      de horário ainda não aceita e agendamento cancelado não contam —
      nesses casos o follow-up ajuda a fechar/retomar o agendamento.
"""
from __future__ import annotations

from dataclasses import dataclass
from src.agents.contexto_conversa import descrever_interesse
from src.config import settings
from src.domain.entities import Lead, RemetenteMensagem
from src.domain.interfaces import ILeadRepository, ILLMProvider, INotifier
from src.domain.midia import remover_marcadores

MAX_FOLLOWUPS = 2
# Negócio pendente (compra, aluguel, investimento ou venda do próprio imóvel
# parados no meio): insiste um pouco mais — inclusive se o cliente encerrou a
# conversa —, sempre com argumentos reais e respeitando quem pediu para parar.
MAX_FOLLOWUPS_NEGOCIO_PENDENTE = 3

_PROMPT_SISTEMA = (
    f"Você é {settings.agente_nome}, um agente imobiliário (SDR) humanizado "
    "reengajando um lead que parou de responder. Escreva uma mensagem curta "
    "(1-2 frases), simpática e sem soar robótica, retomando EXATAMENTE o ponto "
    "em que a conversa parou (ex.: a última pergunta feita, o imóvel mostrado, "
    "o horário proposto) e convidando o lead a continuar. Use o primeiro nome "
    "do lead se souber. Não invente imóveis, valores nem horários."
)

_PROMPT_RETOMADA = (
    f"Você é {settings.agente_nome}, um agente imobiliário (SDR) humanizado. O cliente deixou um negócio "
    "pela metade (e pode ter encerrado a conversa). Escreva uma mensagem curta (2-4 frases), calorosa e "
    "PERSUASIVA, convidando-o a retomar: mostre por que vale a pena usando SOMENTE os argumentos (fatos) "
    "fornecidos, com números quando houver. Seja honesto: nada de urgência falsa, pressão ou promessas que "
    "não estão nos fatos. Use o primeiro nome do lead e termine com uma pergunta simples para ele responder."
)


def tem_agendamento(lead: Lead) -> bool:
    """Visita/reunião efetivamente marcada: confirmada pelo cliente ou
    remarcada pelo corretor (aguardando o cliente ver o novo horário)."""
    return any(
        a.status == "confirmado" or (a.status == "sugerido" and a.horario_anterior)
        for a in lead.agendamentos
    )


@dataclass
class FollowUpEnviado:
    lead: Lead
    mensagem: str


class FollowUpAgent:
    def __init__(
        self,
        llm_provider: ILLMProvider,
        lead_repository: ILeadRepository,
        notifier: INotifier | None = None,
        argumentos=None,  # Callable[[Lead], list[str]] — fatos de negócio (ArgumentosFollowUp)
    ) -> None:
        self._argumentos = argumentos
        self._llm = llm_provider
        self._leads = lead_repository
        self._notifier = notifier

    def executar_para_leads_inativos(self, minutos_inatividade: int = 60) -> list[FollowUpEnviado]:
        enviados: list[FollowUpEnviado] = []
        for lead in self._leads.listar_aguardando_followup(minutos_inatividade):
            if not self._deve_reengajar(lead):
                continue
            enviados.append(self.reengajar(lead))
        return enviados

    def reengajar(self, lead: Lead) -> FollowUpEnviado:
        """Gera e registra o follow-up de UM lead, sem checar inatividade
        (usado pelo comando /followup do bot para demonstração ao vivo)."""
        mensagem = self._gerar_mensagem(lead)
        lead.registrar_mensagem(RemetenteMensagem.AGENTE, mensagem)
        lead.followups_enviados += 1
        self._leads.salvar(lead)

        if self._notifier and lead.canal == "telegram":
            self._notifier.enviar(lead.id, mensagem)
        return FollowUpEnviado(lead, mensagem)

    @staticmethod
    def _deve_reengajar(lead: Lead) -> bool:
        from src.services.argumentos_followup import assunto_pendente

        if not lead.historico or lead.nao_contatar or tem_agendamento(lead) and not lead.captacao:
            return False
        pendente = assunto_pendente(lead)
        if lead.atendimento_encerrado and not pendente:
            return False  # encerrou e não ficou nada pela metade: não insiste
        limite = MAX_FOLLOWUPS_NEGOCIO_PENDENTE if pendente else MAX_FOLLOWUPS
        return lead.followups_enviados < limite

    def _gerar_mensagem(self, lead: Lead) -> str:
        ultimas = "\n".join(
            f"{m.remetente.value}: {remover_marcadores(m.conteudo)}" for m in lead.historico[-6:]
        )
        perfil = lead.perfil
        interesse = descrever_interesse(
            {
                "intencao": perfil.intencao.value,
                "regiao_interesse": perfil.regiao_interesse,
                "quartos_desejados": perfil.quartos_desejados,
                "faixa_preco_max": perfil.faixa_preco_max,
                "ticket_investimento": perfil.ticket_investimento,
                "expectativa_retorno": perfil.expectativa_retorno,
            }
        )
        argumentos = self._argumentos(lead) if self._argumentos else []
        if lead.captacao:
            finalidade = "vender" if lead.captacao.get("finalidade") == "venda" else "colocar para alugar"
            interesse = f"{finalidade} o próprio imóvel (cadastro parado no meio)"
        contexto = (
            f"Nome do lead: {lead.nome or 'não informado'}\n"
            f"Interesse já identificado: {interesse or 'ainda não identificado'}\n"
            f"Encerrou a conversa: {'sim' if lead.atendimento_encerrado else 'não'}\n"
            f"Este é o follow-up nº {lead.followups_enviados + 1}.\n"
        )
        if argumentos:
            contexto += "Argumentos (fatos reais da nossa base, use-os):\n" + "\n".join(f"- {a}" for a in argumentos) + "\n"
            contexto += f"\nHistórico recente (follow-up de retomada):\n{ultimas}"
            prompt = _PROMPT_RETOMADA
        else:
            contexto += f"\nHistórico recente (follow-up de reengajamento):\n{ultimas}"
            prompt = _PROMPT_SISTEMA
        return self._llm.gerar_resposta(
            [{"role": "system", "content": prompt}, {"role": "user", "content": contexto}],
            temperatura=0.6,
        )
