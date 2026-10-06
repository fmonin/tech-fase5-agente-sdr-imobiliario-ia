"""Testes das entidades de domínio (não dependem de LLM, banco ou rede)."""
from src.domain.entities import (
    IntencaoLead,
    Lead,
    PerfilLead,
    RemetenteMensagem,
)


def test_lead_registrar_mensagem_atualiza_historico():
    lead = Lead()
    lead.registrar_mensagem(RemetenteMensagem.LEAD, "Olá, procuro apartamento")

    assert len(lead.historico) == 1
    assert lead.historico[0].conteudo == "Olá, procuro apartamento"


def test_lead_aguardando_resposta_apos_mensagem_do_agente():
    lead = Lead()
    lead.registrar_mensagem(RemetenteMensagem.LEAD, "Oi")
    lead.registrar_mensagem(RemetenteMensagem.AGENTE, "Olá! Como posso ajudar?")

    assert lead.aguardando_resposta_desde is not None


def test_perfil_dados_essenciais_completos_compra():
    perfil = PerfilLead(
        intencao=IntencaoLead.COMPRA,
        regiao_interesse="Zona Sul",
        quartos_desejados=2,
        faixa_preco_max=500000,
    )
    assert perfil.dados_essenciais_completos() is True


def test_perfil_dados_essenciais_incompletos_investimento():
    perfil = PerfilLead(intencao=IntencaoLead.INVESTIMENTO, ticket_investimento=300000)
    assert perfil.dados_essenciais_completos() is False
