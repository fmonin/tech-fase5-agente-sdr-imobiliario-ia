"""Agente Esclarecedor.

Responsabilidade única: quando ainda faltam dados para qualificar o lead
(ex.: não sabemos a região de interesse ou o número de quartos), este
agente redige a próxima pergunta de forma natural e humanizada — mantendo
a conversa fluida em vez de um formulário robótico.

Segue o mesmo princípio já usado no `ConsultorImoveisAgent`/
`ConsultaAgendaAgent`: descobrir QUAL campo perguntar é feito de forma
determinística em Python (`_determinar_campo_faltante`), e só a REDAÇÃO da
pergunta é responsabilidade do LLM. Isso evita um problema real que já
aconteceu aqui: se essa decisão dependesse de reconhecer palavras-chave só
na última mensagem do lead (ex.: "investir"), um erro de digitação
("invetis") fazia o agente "esquecer" que já sabia a intenção e voltar
para a pergunta genérica inicial — um loop confuso para o usuário.
"""
from __future__ import annotations

from src.agents.contexto_conversa import historico_anterior
from src.agents.state import EstadoConversa
from src.config import settings
from src.domain.interfaces import ILLMProvider

_PROMPT_SISTEMA = (
    f"Você é {settings.agente_nome}, um agente imobiliário (SDR) humanizado "
    "conversando com um lead. Faça UMA pergunta curta e natural pedindo "
    "EXATAMENTE o campo indicado em 'Próximo campo a perguntar' abaixo — "
    "nunca repita uma pergunta sobre algo que já está preenchido em 'Já "
    "sabemos'; só pergunte se é compra ou aluguel quando o campo pedido for "
    "'intencao'. Você tem acesso ao histórico da "
    "conversa: se o lead está retomando um atendimento anterior, reconheça "
    "brevemente o que ele já informou antes de fazer a pergunta."
)


class EsclarecedorAgent:
    def __init__(self, llm_provider: ILLMProvider) -> None:
        self._llm = llm_provider

    def __call__(self, estado: EstadoConversa) -> dict:
        campo_faltante = self._determinar_campo_faltante(estado)
        contexto = (
            f"Intenção identificada: {estado.get('intencao', 'indefinida')}\n"
            f"Já sabemos: região={estado.get('regiao_interesse')}, "
            f"quartos={estado.get('quartos_desejados')}, "
            f"faixa_preco=({estado.get('faixa_preco_min')}, {estado.get('faixa_preco_max')}), "
            f"ticket_investimento={estado.get('ticket_investimento')}, "
            f"expectativa_retorno={estado.get('expectativa_retorno')}\n"
            + self._contexto_extra(estado)
            + f"Próximo campo a perguntar: {campo_faltante}\n"
            f"Última mensagem do lead: {estado['mensagem_usuario']}"
        )
        mensagens = [{"role": "system", "content": _PROMPT_SISTEMA}]
        mensagens.extend(historico_anterior(estado))
        mensagens.append({"role": "user", "content": contexto})
        resposta = self._llm.gerar_resposta(mensagens, temperatura=0.5)
        return {"resposta_agente": resposta}

    @staticmethod
    def _contexto_extra(estado: EstadoConversa) -> str:
        """Quando o lead abre uma NOVA busca ("quero alugar também"), o agente
        reconhece que a busca anterior e os agendamentos pendentes continuam
        valendo, em vez de agir como se a conversa tivesse recomeçado."""
        if estado.get("recomecar_busca"):
            return (
                "O lead NÃO quer continuar a busca anterior e quer outra coisa: diga que tudo bem "
                "(a busca anterior fica guardada) e pergunte de forma aberta o que ele procura agora "
                "— comprar, alugar ou investir.\n"
            )
        if not estado.get("busca_anterior"):
            return ""
        pendentes = [a for a in estado.get("agendamentos_anteriores") or [] if "(sugerido)" in a]
        texto = (
            f"NOVA BUSCA: o lead começou agora uma nova busca; a anterior ({estado['busca_anterior']}) "
            "continua registrada para o corretor — diga isso em meia frase antes da pergunta.\n"
        )
        if pendentes:
            texto += (
                f"Agendamento aguardando confirmação do lead: {pendentes[-1]} — lembre em meia frase "
                "que ele pode confirmar depois.\n"
            )
        return texto

    @staticmethod
    def _determinar_campo_faltante(estado: EstadoConversa) -> str:
        """Decide, com regras simples e auditáveis, qual é o próximo dado
        que falta coletar — a mesma decisão que `dados_completos`
        (`QualificadorAgent`) usa para saber que ainda falta algo, só que
        aqui detalhada campo a campo."""
        if estado.get("intencao") == "investimento":
            if not estado.get("ticket_investimento"):
                return "ticket_investimento (quanto o lead pretende investir)"
            if not estado.get("expectativa_retorno"):
                return "expectativa_retorno (qual retorno mensal ele espera — em % ao mês ou valor de aluguel em R$)"
            return "nenhum — dados completos"

        if estado.get("intencao") not in ("compra", "aluguel"):
            return "intencao (se ele quer comprar, alugar ou investir em um imóvel)"
        if not estado.get("regiao_interesse"):
            return "regiao_interesse (em qual região/bairro ele procura)"
        if estado.get("quartos_desejados") is None:
            return "quartos_desejados (quantos quartos ele precisa)"
        if not (estado.get("faixa_preco_min") or estado.get("faixa_preco_max")):
            return "faixa_preco (qual o orçamento dele)"
        if not estado.get("urgencia"):
            return "urgencia (para quando ele precisa do imóvel / em quanto tempo pretende se mudar)"
        return "nenhum — dados completos"
