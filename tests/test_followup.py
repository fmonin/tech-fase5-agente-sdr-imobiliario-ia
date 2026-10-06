"""Follow-up automático (Exemplo 3 do desafio): retoma o contato, mantém o
contexto, não vira spam e chega ao chat certo do Telegram."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.agents.followup_agent import MAX_FOLLOWUPS, MAX_FOLLOWUPS_NEGOCIO_PENDENTE, FollowUpAgent
from src.domain.entities import Agendamento, IntencaoLead, Lead, RemetenteMensagem
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
from telegram_bot import SessoesTelegram, executar_followup


def _lead_inativo(repo, minutos=90, **kw):
    lead = Lead(nome="Fernando Monin", cpf="52998224725", **kw)
    lead.perfil.intencao = IntencaoLead.COMPRA
    lead.perfil.regiao_interesse = "Mooca"
    lead.registrar_mensagem(RemetenteMensagem.LEAD, "quero comprar na Mooca")
    lead.registrar_mensagem(RemetenteMensagem.AGENTE, "Quantos quartos você precisa?")
    lead.aguardando_resposta_desde = datetime.utcnow() - timedelta(minutes=minutos)
    repo.salvar(lead)
    return lead


def test_reengaja_com_contexto_e_limita_tentativas(tmp_path):
    repo = SqliteLeadRepository(tmp_path / "sdr.db")
    lead = _lead_inativo(repo)
    agente = FollowUpAgent(MockLLMProvider(), repo)

    enviados = agente.executar_para_leads_inativos(60)
    assert len(enviados) == 1 and enviados[0].mensagem.startswith("Oi, Fernando!")
    salvo = repo.buscar_por_id(lead.id)
    assert salvo.historico[-1].conteudo == enviados[0].mensagem and salvo.followups_enviados == 1

    # Ainda sem resposta: negócio pela metade (compra) insiste no máximo
    # MAX_FOLLOWUPS_NEGOCIO_PENDENTE vezes
    for _ in range(5):
        salvo = repo.buscar_por_id(lead.id)
        salvo.aguardando_resposta_desde = datetime.utcnow() - timedelta(minutes=90)
        repo.salvar(salvo)
        agente.executar_para_leads_inativos(60)
    assert repo.buscar_por_id(lead.id).followups_enviados == MAX_FOLLOWUPS_NEGOCIO_PENDENTE > MAX_FOLLOWUPS

    # O lead respondeu: o contador zera
    salvo = repo.buscar_por_id(lead.id)
    salvo.registrar_mensagem(RemetenteMensagem.LEAD, "2 quartos")
    assert salvo.followups_enviados == 0


def test_nao_reengaja_lead_recente_nem_com_visita_confirmada(tmp_path):
    repo = SqliteLeadRepository(tmp_path / "sdr.db")
    _lead_inativo(repo, minutos=10)
    com_visita = _lead_inativo(repo)
    com_visita.agendamentos.append(
        Agendamento(lead_id=com_visita.id, quando_sugerido="amanhã", status="confirmado",
                    data_hora=datetime.utcnow() + timedelta(days=1))
    )
    repo.salvar(com_visita)
    assert FollowUpAgent(MockLLMProvider(), repo).executar_para_leads_inativos(60) == []


def test_followup_vai_para_o_chat_do_telegram(tmp_path):
    repo = SqliteLeadRepository(tmp_path / "sdr.db")
    lead = _lead_inativo(repo)
    sessoes = SessoesTelegram(tmp_path / "sessoes.json")
    sessoes.associar("6329128990", lead.id)
    container = SimpleNamespace(followup_agent=FollowUpAgent(MockLLMProvider(), repo))
    envios = executar_followup(container, sessoes, 60)
    assert envios and envios[0][0] == "6329128990"


def test_comando_followup_e_eventos(tmp_path):
    """/followup dispara na hora para o lead do chat e o ciclo automático
    registra o evento `followup_disparado` (aparece no dashboard)."""
    from telegram_bot import followup_do_chat

    repo = SqliteLeadRepository(tmp_path / "sdr.db")
    eventos = []
    container = SimpleNamespace(
        followup_agent=FollowUpAgent(MockLLMProvider(), repo),
        conversation_service=SimpleNamespace(buscar_lead=repo.buscar_por_id),
        evento_store=SimpleNamespace(registrar_evento=lambda nome, dados: eventos.append((nome, dados))),
    )
    sessoes = SessoesTelegram(tmp_path / "sessoes.json")
    assert followup_do_chat(container, sessoes, "123") is None  # chat sem conversa

    lead = _lead_inativo(repo, minutos=0)  # acabou de conversar
    sessoes.associar("123", lead.id)
    mensagem = followup_do_chat(container, sessoes, "123")
    assert mensagem and mensagem.startswith("Oi, Fernando!")
    assert repo.buscar_por_id(lead.id).followups_enviados == 1
    assert eventos[-1] == ("followup_disparado", {"lead_id": lead.id, "telegram": True, "origem": "manual"})

    _lead_inativo(repo, minutos=90)
    assert executar_followup(container, sessoes, 60) == []  # lead sem chat no Telegram
    assert eventos[-1][1]["origem"] == "automatico" and eventos[-1][1]["telegram"] is False


def test_followup_so_para_conversas_sem_agendamento(tmp_path):
    from src.agents.followup_agent import tem_agendamento

    def _com(status, **kw):
        lead = Lead()
        lead.agendamentos.append(Agendamento(quando_sugerido="sexta 14h", status=status, **kw))
        return lead

    passado = datetime.utcnow() - timedelta(days=2)
    assert tem_agendamento(_com("confirmado", data_hora=passado))  # mesmo já realizado
    assert tem_agendamento(_com("sugerido", horario_anterior="quinta 10h"))  # remarcado pelo corretor
    assert not tem_agendamento(_com("sugerido"))  # só proposta, cliente não aceitou
    assert not tem_agendamento(_com("cancelado"))
    assert not tem_agendamento(Lead())
