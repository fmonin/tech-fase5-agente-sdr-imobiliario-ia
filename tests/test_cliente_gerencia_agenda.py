"""O próprio cliente consulta, cancela e remarca os seus agendamentos pelo
chat (AgendaClienteAgent)."""
from datetime import datetime, timedelta

from src.domain.entities import Agendamento
from tests.test_agendas_do_cliente import CPF, _cliente_com_agendas


def _falar(servico, lead, *mensagens):
    for m in mensagens:
        lead = servico.processar_mensagem(lead, m)
    return lead, lead.historico[-1].conteudo


def _ativos(agenda):
    agora = datetime.now() - timedelta(days=1)
    return {a.corretor_nome: a for a in agenda.listar_por_cliente(CPF)
            if a.status != "cancelado" and a.data_hora and a.data_hora > agora}


def _dia(dias: int, hora: int) -> tuple[str, datetime]:
    d = (datetime.now() + timedelta(days=dias)).replace(hour=hora, minute=0, second=0, microsecond=0)
    return f"{d:%d/%m} às {hora}h", d


def test_consultar_e_cancelar_com_confirmacao(tmp_path):
    servico, agenda, lead = _cliente_com_agendas(tmp_path)
    lead, texto = _falar(servico, lead, "quais são meus agendamentos?")
    assert "Pedro Almeida" in texto and "Ricardo Moura" in texto and "remarcar ou cancelar" in texto

    lead, texto = _falar(servico, lead, "quero cancelar minha visita")
    assert "confirma o cancelamento" in texto and "Pedro Almeida" in texto and "[[AGENDA:" in texto
    assert "Pedro Almeida" in _ativos(agenda)  # nada muda sem confirmar
    lead, texto = _falar(servico, lead, "sim")
    assert texto.startswith("Pronto, cancelei") and "Já avisei o(a) corretor(a) Pedro Almeida" in texto
    cancelado = next(a for a in agenda.listar_por_cliente(CPF) if a.corretor_nome == "Pedro Almeida" and a.status == "cancelado"
                     and a.cancelado_por == "cliente")
    assert cancelado.cliente_notificado  # o cliente não recebe "aviso" do próprio cancelamento
    assert set(_ativos(agenda)) == {"Ricardo Moura"}

    lead, texto = _falar(servico, lead, "e quais os meus agendamentos agora?")
    assert "Ricardo Moura" in texto and "Pedro" not in texto


def test_escolher_qual_cancelar_e_desistir(tmp_path):
    servico, agenda, lead = _cliente_com_agendas(tmp_path)
    lead, texto = _falar(servico, lead, "quero cancelar um agendamento")
    assert "Qual deles você quer cancelar" in texto
    lead, texto = _falar(servico, lead, "o 2")
    assert "confirma o cancelamento" in texto and "Ricardo Moura" in texto
    lead, texto = _falar(servico, lead, "não")
    assert "mantive" in texto
    assert set(_ativos(agenda)) == {"Pedro Almeida", "Ricardo Moura"}


def test_remarcar_direto_com_novo_horario(tmp_path):
    servico, agenda, lead = _cliente_com_agendas(tmp_path)
    novo_txt, novo_dt = _dia(10, 15)
    lead, texto = _falar(servico, lead, f"quero remarcar a reunião com o Ricardo para {novo_txt}")
    assert "Remarquei" in texto and "Ricardo Moura" in texto
    reuniao = _ativos(agenda)["Ricardo Moura"]
    assert reuniao.data_hora == novo_dt and reuniao.status == "confirmado" and reuniao.horario_anterior
    assert reuniao.cliente_notificado  # sem aviso de "o corretor remarcou"
    assert _ativos(agenda)["Pedro Almeida"].data_hora != novo_dt


def test_remarcar_perguntando_qual_e_horario(tmp_path):
    servico, agenda, lead = _cliente_com_agendas(tmp_path)
    lead, texto = _falar(servico, lead, "preciso mudar a data de um agendamento")
    assert "Qual deles você quer remarcar" in texto
    lead, texto = _falar(servico, lead, "a 1")
    assert "Para qual dia e horário" in texto and "Pedro Almeida" in texto
    novo_txt, novo_dt = _dia(12, 16)
    lead, texto = _falar(servico, lead, novo_txt)
    assert "Remarquei" in texto
    assert _ativos(agenda)["Pedro Almeida"].data_hora == novo_dt


def test_remarcar_em_horario_ocupado_do_corretor(tmp_path):
    servico, agenda, lead = _cliente_com_agendas(tmp_path)
    novo_txt, novo_dt = _dia(9, 11)
    agenda.registrar(Agendamento(lead_id="outro", cliente_cpf="11144477735", quando_sugerido="x", status="confirmado",
                                 data_hora=novo_dt, corretor_id="COR001", corretor_nome="Pedro Almeida"))
    lead, texto = _falar(servico, lead, f"remarca minha visita para {novo_txt}")
    assert "já tem um compromisso" in texto
    assert _ativos(agenda)["Pedro Almeida"].data_hora != novo_dt
    outro_txt, outro_dt = _dia(9, 17)
    lead, texto = _falar(servico, lead, outro_txt)
    assert "Remarquei" in texto and _ativos(agenda)["Pedro Almeida"].data_hora == outro_dt


def test_sem_agendamentos(tmp_path):
    from tests.test_detalhe_imovel import _servico

    servico, _ = _servico(tmp_path)
    lead = servico.obter_ou_criar_lead(None)
    lead, _ = _falar(servico, lead, "Sou Fernando Monin, CPF 529.982.247-25")
    lead, texto = _falar(servico, lead, "quero cancelar minha visita")
    assert "Não encontrei nenhum agendamento ativo" in texto
    lead, texto = _falar(servico, lead, "quero comprar um apartamento na Mooca")  # conversa segue normal
    assert "[[AGENDA" not in texto


def test_corretor_e_avisado_quando_o_cliente_cancela_ou_remarca(tmp_path):
    from src.infrastructure.crm.mock_crm import MockCRM
    from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
    from src.services.agenda_service import AgendaService

    servico, agenda, lead = _cliente_com_agendas(tmp_path)
    agenda_service = AgendaService(agenda, SqliteLeadRepository(tmp_path / "sdr.db"), MockCRM(tmp_path / "crm.json"))
    assert agenda_service.avisos_do_cliente_para_corretor("COR001") == []  # nada mudou ainda

    lead, _ = _falar(servico, lead, "quero cancelar minha visita", "sim")
    [aviso] = agenda_service.avisos_do_cliente_para_corretor("COR001")
    assert aviso.startswith("❌ Fernando Monin cancelou a visita") and "Apartamento na Mooca" in aviso
    assert agenda_service.avisos_do_cliente_para_corretor("COR001") == []  # avisa uma única vez
    assert agenda_service.avisos_do_cliente_para_corretor("COR009") == []  # outro corretor não é afetado

    novo_txt, _ = _dia(11, 9)
    lead, _ = _falar(servico, lead, f"remarca a reunião para {novo_txt}")
    [aviso] = agenda_service.avisos_do_cliente_para_corretor("COR009")
    assert aviso.startswith("🔁 Fernando Monin remarcou a reunião") and "para dia" in aviso
