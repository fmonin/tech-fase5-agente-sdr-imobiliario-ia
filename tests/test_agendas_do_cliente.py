"""Ao "logar" (CPF no chat ou /start no Telegram), o cliente vê todas as
agendas ativas dele, cada uma com o(a) corretor(a) responsável."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.domain.entities import Agendamento
from telegram_bot import SessoesTelegram, boas_vindas_de_volta
from tests.test_detalhe_imovel import _servico

CPF = "52998224725"


def _cliente_com_agendas(tmp_path):
    servico, agenda = _servico(tmp_path)
    lead = servico.obter_ou_criar_lead(None)
    lead = servico.processar_mensagem(lead, "Sou Fernando Monin, CPF 529.982.247-25")
    futuro = datetime.utcnow() + timedelta(days=3)
    base = dict(lead_id=lead.id, cliente_cpf=CPF, cliente_nome="Fernando Monin")
    agenda.registrar(Agendamento(**base, tipo="visita", status="confirmado", data_hora=futuro.replace(hour=14, minute=0),
                                 quando_sugerido="x", corretor_id="COR001", corretor_nome="Pedro Almeida",
                                 imovel_titulo="Apartamento na Mooca"))
    agenda.registrar(Agendamento(**base, tipo="reuniao", status="sugerido", data_hora=futuro.replace(hour=10, minute=0)
                                 + timedelta(days=1), quando_sugerido="x", corretor_id="COR009",
                                 corretor_nome="Ricardo Moura"))
    agenda.registrar(Agendamento(**base, status="cancelado", data_hora=futuro, quando_sugerido="x",
                                 corretor_id="COR002", corretor_nome="Joaquim Ribeiro", cliente_notificado=True))
    agenda.registrar(Agendamento(**base, status="confirmado", data_hora=datetime.utcnow() - timedelta(days=5),
                                 quando_sugerido="x", corretor_id="COR004", corretor_nome="Camila Duarte"))
    return servico, agenda, lead


def test_login_por_cpf_mostra_todas_as_agendas_com_corretores(tmp_path):
    servico, _, _ = _cliente_com_agendas(tmp_path)
    novo = servico.obter_ou_criar_lead(None)
    novo = servico.processar_mensagem(novo, "Olá")
    novo = servico.processar_mensagem(novo, "meu CPF é 529.982.247-25")
    lead = servico.buscar_lead(novo.id) or novo
    texto = lead.historico[-1].conteudo
    assert "Que bom te ver de novo" in texto
    assert "Você tem 2 agendamentos comigo" in texto
    assert "Pedro Almeida" in texto and "(11) 98888-0001" in texto and "✅ confirmada" in texto
    assert "Ricardo Moura" in texto and "⏳ aguardando sua confirmação" in texto
    assert "Apartamento na Mooca" in texto
    assert "Joaquim" not in texto and "Camila" not in texto  # cancelada e já passada ficam de fora
    assert texto.index("Pedro") < texto.index("Ricardo")  # da mais próxima para a mais distante


def test_start_no_telegram_mostra_agendas_do_cliente_ja_identificado(tmp_path):
    servico, agenda, lead = _cliente_com_agendas(tmp_path)
    from src.infrastructure.repositories.sqlite_corretor_repository import SqliteCorretorRepository
    from pathlib import Path

    container = SimpleNamespace(
        conversation_service=servico, agenda_repository=agenda,
        corretor_repository=SqliteCorretorRepository(caminho_db=tmp_path / "sdr.db",
                                                     caminho_seed_json=Path("data/corretores.json")),
    )
    sessoes = SessoesTelegram(tmp_path / "sessoes.json")
    assert boas_vindas_de_volta(container, sessoes, "777") is None  # chat novo: fluxo normal (pede CPF)
    sessoes.associar("777", lead.id)
    texto = boas_vindas_de_volta(container, sessoes, "777")
    assert texto.startswith("Olá de novo, Fernando!")
    assert "Pedro Almeida" in texto and "Ricardo Moura" in texto and "Joaquim" not in texto
