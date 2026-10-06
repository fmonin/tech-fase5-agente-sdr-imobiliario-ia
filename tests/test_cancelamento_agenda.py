"""Cancelamento de agendamento pelo corretor + aviso ao cliente ao se
identificar + saudação de login do corretor com a agenda."""
from datetime import datetime, timedelta
from pathlib import Path

from src.agents.agenda_query_agent import ConsultaAgendaAgent
from src.agents.identification_agent import IdentificacaoAgent
from src.domain.entities import Agendamento, Lead
from src.infrastructure.crm.mock_crm import MockCRM
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
from src.infrastructure.repositories.sqlite_corretor_repository import SqliteCorretorRepository
from src.services.agenda_service import AgendaService

CPF = "52998224725"


def _cenario(tmp_path):
    db = tmp_path / "sdr.db"
    leads = SqliteLeadRepository(db)
    agenda = SqliteAgendaRepository(caminho_db=db)
    corretores = SqliteCorretorRepository(caminho_db=db, caminho_seed_json=Path("data/corretores.json"))
    corretor = corretores.listar_todos()[0]

    lead = Lead(nome="Fernando Monin", cpf=CPF)
    agendamento = Agendamento(
        lead_id=lead.id, quando_sugerido="amanhã às 15h", tipo="reuniao", status="confirmado",
        data_hora=(datetime.utcnow() + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0),
        cliente_cpf=CPF, cliente_nome="Fernando Monin",
        corretor_id=corretor.id, corretor_nome=corretor.nome, imovel_titulo="Garden no Ipiranga",
    )
    lead.agendamentos.append(
        Agendamento(lead_id=lead.id, quando_sugerido="amanhã às 15h", status="confirmado")
    )
    leads.salvar(lead)
    agenda.registrar(agendamento)
    servico = AgendaService(agenda, leads, MockCRM())
    return servico, agenda, leads, corretor, lead, agendamento


def test_corretor_cancela_e_historico_do_lead_e_atualizado(tmp_path):
    servico, agenda, leads, corretor, lead, agendamento = _cenario(tmp_path)
    assert len(servico.agendamentos_ativos(corretor.id)) == 1

    resultado = servico.cancelar_pelo_corretor(agendamento.id, corretor.id, "imprevisto na agenda")

    assert resultado.sucesso
    salvo = agenda.buscar_por_id(agendamento.id)
    assert salvo.status == "cancelado" and salvo.cancelado_por == "corretor"
    assert salvo.motivo_cancelamento == "imprevisto na agenda"
    assert servico.agendamentos_ativos(corretor.id) == []
    assert agenda.contar_agendamentos_ativos(corretor.id) == 0
    assert leads.buscar_por_id(lead.id).agendamentos[0].status == "cancelado"


def test_outro_corretor_nao_pode_cancelar(tmp_path):
    servico, agenda, *_ , agendamento = _cenario(tmp_path)
    resultado = servico.cancelar_pelo_corretor(agendamento.id, "outro-corretor")
    assert not resultado.sucesso
    assert agenda.buscar_por_id(agendamento.id).status == "confirmado"


def test_cliente_e_avisado_uma_unica_vez_ao_se_identificar(tmp_path):
    servico, agenda, leads, corretor, lead, agendamento = _cenario(tmp_path)
    servico.cancelar_pelo_corretor(agendamento.id, corretor.id, "imprevisto na agenda")
    agente = IdentificacaoAgent(leads, agenda)

    estado = {"lead_id": "lead-temporario", "mensagem_usuario": "meu CPF é 529.982.247-25", "historico_mensagens": []}
    resposta = agente(estado)["resposta_agente"]
    assert "Um aviso importante" in resposta
    assert corretor.nome in resposta and "imprevisto na agenda" in resposta
    assert "agende um novo horário" in resposta

    # Na próxima identificação, o aviso não se repete.
    resposta2 = agente(estado)["resposta_agente"]
    assert "Um aviso importante" not in resposta2


def test_saudacao_de_login_mostra_a_agenda(tmp_path):
    servico, agenda, leads, corretor, lead, agendamento = _cenario(tmp_path)
    texto = ConsultaAgendaAgent(MockLLMProvider(), agenda).saudar(corretor)
    assert "Fernando Monin" in texto and "Garden no Ipiranga" in texto

    servico.cancelar_pelo_corretor(agendamento.id, corretor.id)
    texto_vazio = ConsultaAgendaAgent(MockLLMProvider(), agenda).saudar(corretor)
    assert "Fernando Monin" not in texto_vazio


def test_migracao_de_banco_antigo(tmp_path):
    import sqlite3

    db = tmp_path / "antigo.db"
    with sqlite3.connect(db) as conexao:  # tabela no formato antigo, sem colunas de cancelamento
        conexao.execute(
            "CREATE TABLE agendamentos (id TEXT PRIMARY KEY, lead_id TEXT NOT NULL, quando_sugerido TEXT NOT NULL,"
            " tipo TEXT NOT NULL, status TEXT NOT NULL, data_hora TEXT, cliente_cpf TEXT, cliente_nome TEXT,"
            " corretor_id TEXT, corretor_nome TEXT, imovel_id TEXT, imovel_titulo TEXT, criado_em TEXT NOT NULL)"
        )
        conexao.execute(
            "INSERT INTO agendamentos VALUES ('a1','l1','amanhã','visita','sugerido',NULL,?,NULL,'c1',NULL,NULL,NULL,?)",
            (CPF, datetime.utcnow().isoformat()),
        )
    agenda = SqliteAgendaRepository(caminho_db=db)
    assert agenda.buscar_por_id("a1").cliente_notificado is False
