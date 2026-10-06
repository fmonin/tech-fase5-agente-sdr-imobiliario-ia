"""Agente Resumidor.

Responsabilidade única: gerar um resumo inteligente e objetivo da conversa
para que o corretor humano assuma o atendimento, mantendo todo o contexto.
"""
from __future__ import annotations

from src.agents.contexto_conversa import formatar_historico
from src.agents.state import EstadoConversa
from src.domain.entities import IntencaoLead, ResumoCorretor, TemperaturaLead
from src.domain.interfaces import ICRM, ILLMProvider

_SCHEMA_PROMPT_SISTEMA = (
    "Você escreve resumos objetivos (máx. 4 frases) para corretores de imóveis "
    "assumirem uma conversa com um lead. Inclua: intenção, principais "
    "preferências coletadas e qual deve ser o próximo passo do corretor."
)


class ResumidorAgent:
    def __init__(self, llm_provider: ILLMProvider, crm: ICRM) -> None:
        self._llm = llm_provider
        self._crm = crm

    def __call__(self, estado: EstadoConversa) -> dict:
        contexto = self._montar_contexto(estado)
        texto_resumo = self._llm.gerar_resposta(
            [
                {"role": "system", "content": _SCHEMA_PROMPT_SISTEMA},
                {"role": "user", "content": contexto},
            ],
            temperatura=0.2,
        )

        resumo = ResumoCorretor(
            lead_id=estado["lead_id"],
            texto=texto_resumo,
            intencao=IntencaoLead(estado.get("intencao", "indefinida")),
            temperatura=TemperaturaLead(estado.get("temperatura", "frio")),
            proximos_passos=(
                f"Confirmar agendamento: {estado['agendamento_sugerido']}"
                if estado.get("agendamento_sugerido")
                else "Entrar em contato para dar continuidade à qualificação."
            ),
        )
        self._crm.registrar_resumo(resumo)

        return {"resumo_corretor": texto_resumo}

    @staticmethod
    def _montar_contexto(estado: EstadoConversa) -> str:
        campos = [
            f"Intenção: {estado.get('intencao')}",
            f"Região de interesse: {estado.get('regiao_interesse')}",
            f"Quartos desejados: {estado.get('quartos_desejados')}",
            f"Faixa de preço: {estado.get('faixa_preco_min')} a {estado.get('faixa_preco_max')}",
            f"Ticket de investimento: {estado.get('ticket_investimento')}",
            f"Expectativa de retorno: {estado.get('expectativa_retorno')}",
            f"Urgência: {estado.get('urgencia')}",
            f"Temperatura: {estado.get('temperatura')}",
            f"Agendamento sugerido: {estado.get('agendamento_sugerido')}",
            f"Buscas anteriores do lead: {'; '.join(estado.get('buscas_anteriores') or []) or 'nenhuma'}",
        ]
        historico = formatar_historico(estado)
        if historico:
            campos.append(f"\nTrechos recentes da conversa:\n{historico}")
        return "\n".join(campos)
