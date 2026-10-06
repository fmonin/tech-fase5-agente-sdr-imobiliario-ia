"""Estado compartilhado do grafo multiagente (LangGraph).

No LangGraph, todos os "nós" (agentes) leem e escrevem em um único objeto
de estado compartilhado. Definir esse estado em um arquivo próprio (em vez
de espalhado pelos agentes) segue o Single Responsibility Principle: este
arquivo tem uma única razão para mudar — quando a "forma" da conversa
mudar.

Para um estudante iniciante:
    Pense no `EstadoConversa` como uma "prancheta" que passa de agente em
    agente. Cada agente lê o que precisa e anota o que descobriu/decidiu.
"""
from __future__ import annotations

from typing import Literal, Optional, TypedDict

from src.domain.entities import Agendamento, Imovel


class EstadoConversa(TypedDict, total=False):
    # Entrada
    lead_id: str
    mensagem_usuario: str
    historico_mensagens: list[dict]  # [{"role": "user"/"assistant", "content": str}]

    # Cadastro do cliente (preenchido pelo IdentificacaoAgent — veja
    # src/agents/identification_agent.py). Enquanto `cliente_identificado`
    # for False, o grafo nem chega a rodar os agentes de qualificação.
    cliente_identificado: bool
    cliente_cpf: Optional[str]
    cliente_nome: Optional[str]
    lead_id_recuperado: Optional[str]  # preenchido quando um cliente recorrente é reconhecido

    # Perfil do lead (espelha PerfilLead, em formato simples de dict)
    intencao: str
    faixa_preco_min: Optional[float]
    faixa_preco_max: Optional[float]
    quartos_desejados: Optional[int]
    regiao_interesse: Optional[str]
    urgencia: Optional[str]
    ticket_investimento: Optional[float]
    expectativa_retorno: Optional[str]
    temperatura: str

    # Agendamentos já registrados para o lead (texto pronto), usados pelo
    # RecapituladorAgent para responder "o que já conversamos?".
    agendamentos_anteriores: list[str]

    # Sinalizações usadas pelo roteador (supervisor) para decidir o próximo agente
    quer_agendar: bool
    pediu_resumo: bool
    dados_completos: bool
    pediu_detalhes: bool  # o lead pediu mais informações sobre um imóvel
    imovel_detalhado: bool  # o DetalheImovelAgent conseguiu identificar o imóvel
    imovel_interesse_id: Optional[str]  # imóvel "em foco" na conversa (persistido no perfil)
    nova_busca: bool
    recomecar_busca: bool
    ampliar_busca: bool  # o lead quer ver os bairros vizinhos (ou "não gostei dessas")  # o lead disse que quer "outra coisa": perfil zerado, pergunta a intenção
    busca_anterior: Optional[str]  # interesse anterior quando o lead troca de intenção
    buscas_anteriores: list[str]  # todas as buscas anteriores do lead (para contexto/resumo)  # o lead trocou de intenção nesta mensagem (perfil recomeçado)
    proximo_agente: Literal[
        "qualificador", "consultor_imoveis", "agendador", "resumidor", "fim"
    ]

    # Saídas produzidas pelos agentes ao longo do grafo
    imoveis_sugeridos: list[Imovel]
    resposta_agente: str
    agendamento_sugerido: Optional[str]
    agendamento_data_hora: Optional[str]  # ISO 8601
    agendamento_status: Optional[str]  # "sugerido" | "confirmado"
    agendamento_cancelado: Optional[str]
    agendamento_imovel_titulo: Optional[str]
    agendamento_id: Optional[str]  # id do agendamento na base de agenda (mesmo id no histórico do lead)
    agendamento_cancelado_id: Optional[str]  # horário anterior trocado a pedido do lead
    tipo_agendamento: Optional[str]
    corretor_id: Optional[str]
    corretor_nome: Optional[str]
    resumo_corretor: Optional[str]
    agenda_cliente_respondeu: bool  # o AgendaClienteAgent tratou a mensagem (consultar/cancelar/remarcar)
    agendamento_alterado_pelo_cliente: Optional[Agendamento]
    agendamento_alterado_antes: Optional[str]  # quando_sugerido antes da mudança
    feedback_visita_novo: Optional[dict]  # cliente não gostou da visita (motivo -> próximas sugestões)
    feedback_visitas: list[dict]  # histórico desses retornos, usado pelo Consultor
    captacao: dict  # cadastro do imóvel que o cliente quer vender/alugar (em andamento)
    captacao_respondeu: bool
    captacao_concluida: Optional[dict]
    atendimento_encerrado: bool  # o cliente encerrou o atendimento (sem follow-up até voltar)
    nota_atendimento: Optional[int]  # satisfação 1-5 informada ao encerrar
    nao_contatar: bool  # o cliente pediu para não receber mais mensagens
    agendamento_novo: Optional[Agendamento]  # criado direto como confirmado (ex.: avaliação de captação)
