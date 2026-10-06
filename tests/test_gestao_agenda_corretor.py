"""Cancelar / remarcar compromissos pelo chat do corretor (com confirmação)
e aviso de remarcação ao cliente."""
import json
from datetime import datetime, timedelta
from pathlib import Path

from src.agents.agenda_query_agent import ConsultaAgendaAgent
from src.agents.gestao_agenda_agent import GestaoAgendaCorretorAgent
from src.agents.identification_agent import IdentificacaoAgent
from src.agents.scheduler_agent import AgendadorAgent
from src.domain.entities import Agendamento, Lead
from src.infrastructure.crm.mock_crm import MockCRM
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
from src.infrastructure.repositories.sqlite_corretor_repository import SqliteCorretorRepository
from src.services.agenda_service import AgendaService

CPF_FERNANDO = "52998224725"
CPF_MARIA = "11144477735"


def _amanha_15h():
    return (datetime.utcnow() + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)


def _cenario(tmp_path, llm=None):
    db = tmp_path / "sdr.db"
    leads = SqliteLeadRepository(db)
    agenda = SqliteAgendaRepository(caminho_db=db)
    corretor = SqliteCorretorRepository(caminho_db=db, caminho_seed_json=Path("data/corretores.json")).listar_todos()[0]
    ids = {}
    for nome, cpf, tipo, quando in (
        ("Fernando Monin", CPF_FERNANDO, "reuniao", _amanha_15h()),
        ("Maria Souza", CPF_MARIA, "visita", _amanha_15h() + timedelta(days=3)),
    ):
        lead = Lead(nome=nome, cpf=cpf)
        leads.salvar(lead)
        a = Agendamento(
            lead_id=lead.id, quando_sugerido="x", tipo=tipo, status="confirmado", data_hora=quando,
            cliente_cpf=cpf, cliente_nome=nome, corretor_id=corretor.id, corretor_nome=corretor.nome,
        )
        agenda.registrar(a)
        ids[nome] = (a.id, lead.id)
    llm = llm or MockLLMProvider()
    servico = AgendaService(agenda, leads, MockCRM())
    agente = GestaoAgendaCorretorAgent(llm, servico, ConsultaAgendaAgent(llm, agenda))
    return agente, agenda, leads, corretor, ids


def test_cancelar_pelo_chat_pede_confirmacao_e_cancela(tmp_path):
    agente, agenda, _, corretor, ids = _cenario(tmp_path)
    r1 = agente.processar(corretor, "cancela a reunião de amanhã com o Fernando, tive um imprevisto")
    assert "Só para confirmar" in r1.texto and "Fernando Monin" in r1.texto
    assert "tive um imprevisto" in r1.texto
    assert agenda.buscar_por_id(ids["Fernando Monin"][0]).status == "confirmado"  # nada mudou ainda

    r2 = agente.processar(corretor, "sim", r1.acao_pendente)
    assert r2.agenda_alterada and r2.acao_pendente is None
    cancelado = agenda.buscar_por_id(ids["Fernando Monin"][0])
    assert cancelado.status == "cancelado" and cancelado.motivo_cancelamento == "tive um imprevisto"
    assert agenda.buscar_por_id(ids["Maria Souza"][0]).status == "confirmado"


def test_responder_nao_mantem_o_agendamento(tmp_path):
    agente, agenda, _, corretor, ids = _cenario(tmp_path)
    r1 = agente.processar(corretor, "desmarca a visita da Maria")
    r2 = agente.processar(corretor, "não", r1.acao_pendente)
    assert "não alterei nada" in r2.texto
    assert agenda.buscar_por_id(ids["Maria Souza"][0]).status == "confirmado"


def test_pedido_ambiguo_pergunta_qual_e_aceita_numero(tmp_path):
    agente, agenda, _, corretor, ids = _cenario(tmp_path)
    r1 = agente.processar(corretor, "quero cancelar um compromisso")
    assert "Qual compromisso" in r1.texto and "1." in r1.texto and "2." in r1.texto
    r2 = agente.processar(corretor, "2", r1.acao_pendente)
    assert "Maria Souza" in r2.texto
    agente.processar(corretor, "sim", r2.acao_pendente)
    assert agenda.buscar_por_id(ids["Maria Souza"][0]).status == "cancelado"


def test_remarcar_e_cliente_confirma_novo_horario(tmp_path):
    agente, agenda, leads, corretor, ids = _cenario(tmp_path)
    r1 = agente.processar(corretor, "muda a reunião do Fernando para sábado às 14h")
    assert "remarcar" in r1.texto and "sábado" in r1.texto and "14h" in r1.texto
    r2 = agente.processar(corretor, "sim", r1.acao_pendente)
    assert "Remarquei" in r2.texto

    agendamento_id, lead_id = ids["Fernando Monin"]
    remarcado = agenda.buscar_por_id(agendamento_id)
    assert remarcado.data_hora.weekday() == 5 and remarcado.data_hora.hour == 14
    assert remarcado.status == "sugerido" and remarcado.horario_anterior

    # Cliente se identifica -> é avisado e pode confirmar com "sim".
    estado = {"lead_id": "temp", "mensagem_usuario": "CPF 529.982.247-25", "historico_mensagens": []}
    aviso = IdentificacaoAgent(leads, agenda)(estado)["resposta_agente"]
    assert "precisou remarcar" in aviso and "Esse horário funciona para você?" in aviso

    agendador = AgendadorAgent(MockLLMProvider(), MockCRM(), None, agenda)
    saida = agendador(
        {
            "lead_id": lead_id, "cliente_cpf": CPF_FERNANDO, "mensagem_usuario": "sim",
            "historico_mensagens": [{"role": "assistant", "content": aviso}, {"role": "user", "content": "sim"}],
        }
    )
    assert saida["agendamento_status"] == "confirmado"
    assert agenda.buscar_por_id(agendamento_id).status == "confirmado"


def test_remarcar_sem_horario_pergunta_para_quando(tmp_path):
    agente, agenda, _, corretor, ids = _cenario(tmp_path)
    r1 = agente.processar(corretor, "preciso remarcar a visita da Maria")
    assert "Para quando" in r1.texto
    r2 = agente.processar(corretor, "segunda às 9h", r1.acao_pendente)
    assert "Só para confirmar" in r2.texto
    agente.processar(corretor, "sim", r2.acao_pendente)
    assert agenda.buscar_por_id(ids["Maria Souza"][0]).data_hora.hour == 9


def test_consulta_continua_funcionando(tmp_path):
    agente, *_ , corretor, _ = _cenario(tmp_path)
    resposta = agente.processar(corretor, "o que eu tenho amanhã?")
    assert resposta.acao_pendente is None and "Fernando Monin" in resposta.texto


class _LLMQueInterpreta(MockLLMProvider):
    """Simula o Azure OpenAI devolvendo o JSON de interpretação."""

    def gerar_resposta(self, mensagens, temperatura=0.4):
        if "Pedido:" in mensagens[-1]["content"]:
            return json.dumps({"acao": "cancelar", "numero": 2, "novo_dia_horario": None, "motivo": "cliente desistiu"})
        return super().gerar_resposta(mensagens, temperatura)


def test_interpretacao_pelo_llm(tmp_path):
    agente, agenda, _, corretor, ids = _cenario(tmp_path, llm=_LLMQueInterpreta())
    r1 = agente.processar(corretor, "não vou conseguir atender a Maria")  # sem verbo "cancelar"
    assert "cancelar" in r1.texto and "Maria Souza" in r1.texto and "cliente desistiu" in r1.texto


def test_mudar_de_assunto_descarta_a_acao_pendente(tmp_path):
    agente, agenda, _, corretor, ids = _cenario(tmp_path)
    r1 = agente.processar(corretor, "cancela a visita da Maria")
    r2 = agente.processar(corretor, "o que eu tenho amanhã?", r1.acao_pendente)
    assert r2.acao_pendente is None and "Fernando Monin" in r2.texto
    assert agenda.buscar_por_id(ids["Maria Souza"][0]).status == "confirmado"
