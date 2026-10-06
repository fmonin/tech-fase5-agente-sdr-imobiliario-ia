"""Corretor cancela/remarca -> o cliente é avisado NA HORA (chat + Telegram)."""
from datetime import datetime, timedelta

from src.domain.entities import Agendamento
from src.infrastructure.crm.mock_crm import MockCRM
from src.services.agenda_service import AgendaService
from tests.test_detalhe_imovel import _servico

CPF = "52998224725"


def _cenario(tmp_path, telegram=True):
    servico, agenda = _servico(tmp_path)
    lead = servico.obter_ou_criar_lead(None)
    lead = servico.processar_mensagem(lead, "Sou Fernando Monin, CPF 529.982.247-25")
    a = Agendamento(lead_id=lead.id, cliente_cpf=CPF, cliente_nome="Fernando Monin", tipo="visita", status="confirmado",
                    data_hora=(datetime.now() + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0),
                    quando_sugerido="x", corretor_id="COR001", corretor_nome="Pedro Almeida",
                    imovel_titulo="Apartamento na Mooca")
    agenda.registrar(a)
    enviados = []
    avisar = (lambda lead_id, texto: enviados.append((lead_id, texto)) or True) if telegram else None
    servico_agenda = AgendaService(agenda, servico._leads, MockCRM(tmp_path / "crm.json"), avisar_cliente=avisar)
    return servico, agenda, lead, a, servico_agenda, enviados


def test_cancelamento_avisa_o_cliente_pelo_telegram_e_no_chat(tmp_path):
    servico, agenda, lead, a, servico_agenda, enviados = _cenario(tmp_path)
    r = servico_agenda.cancelar_pelo_corretor(a.id, "COR001", "imprevisto na agenda")
    assert r.sucesso and r.cliente_avisado_por == "telegram"
    [(lead_id, texto)] = enviados
    assert lead_id == lead.id and "Pedro Almeida precisou cancelar a sua visita" in texto
    assert "Motivo: imprevisto na agenda" in texto and "novo horário" in texto
    assert servico._leads.buscar_por_id(lead.id).historico[-1].conteudo == texto
    assert agenda.buscar_por_id(a.id).cliente_notificado  # não repete o aviso no próximo login


def test_sem_telegram_aviso_fica_no_chat_e_repete_no_login(tmp_path):
    servico, agenda, lead, a, servico_agenda, _ = _cenario(tmp_path, telegram=False)
    r = servico_agenda.cancelar_pelo_corretor(a.id, "COR001")
    assert r.cliente_avisado_por == "chat"
    assert "precisou cancelar" in servico._leads.buscar_por_id(lead.id).historico[-1].conteudo
    assert not agenda.buscar_por_id(a.id).cliente_notificado  # avisa de novo ao se identificar


def test_remarcacao_avisa_e_cliente_confirma_com_sim(tmp_path):
    servico, agenda, lead, a, servico_agenda, enviados = _cenario(tmp_path)
    novo = (datetime.now() + timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)
    r = servico_agenda.reagendar_pelo_corretor(a.id, "COR001", novo, f"dia {novo:%d/%m} às 10h", "reunião")
    assert r.sucesso and "precisou remarcar a sua visita" in enviados[-1][1]
    lead = servico.obter_ou_criar_lead(lead.id)
    lead = servico.processar_mensagem(lead, "sim")
    assert agenda.buscar_por_id(a.id).status == "confirmado"
