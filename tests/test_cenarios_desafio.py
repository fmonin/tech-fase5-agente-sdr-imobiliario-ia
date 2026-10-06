"""Cenários do enunciado do Tech Challenge (validação de requisitos).

Exemplo 1 — Compra: entender intenção, perguntar faixa de preço, quartos,
região, identificar urgência e encaminhar para reunião.
Exemplo 2 — Investimento: perfil investidor, ticket, expectativa de retorno,
direcionar para especialista.
+ resumo inteligente para o corretor ao confirmar o agendamento.
(Exemplo 3 — follow-up — está em tests/test_followup.py.)"""
from pathlib import Path

from src.agents.graph import construir_grafo_sdr
from src.agents.summarizer_agent import ResumidorAgent
from src.infrastructure.crm.mock_crm import MockCRM
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
from src.infrastructure.rag.vector_store import TfidfVectorSearch
from src.infrastructure.repositories.json_mapa_bairros import carregar_mapa_bairros
from src.infrastructure.repositories.sqlite_corretor_repository import SqliteCorretorRepository
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository
from src.services.conversation_service import ConversationService


class _Obs:
    def registrar_evento(self, *_):
        pass


def _servico(tmp_path):
    imoveis = SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json="data/imoveis.json")
    leads = SqliteLeadRepository(tmp_path / "sdr.db")
    crm = MockCRM(tmp_path / "crm.json")
    grafo = construir_grafo_sdr(
        llm_provider=MockLLMProvider(), repositorio_imoveis=imoveis,
        busca_semantica=TfidfVectorSearch(imoveis), crm=crm, lead_repository=leads,
        corretor_repository=SqliteCorretorRepository(caminho_db=tmp_path / "sdr.db", caminho_seed_json=Path("data/corretores.json")),
        agenda_repository=SqliteAgendaRepository(caminho_db=tmp_path / "sdr.db"),
        mapa_bairros=carregar_mapa_bairros("data/bairros_sp.json"),
    )
    return ConversationService(grafo, leads, crm, _Obs(), resumidor=ResumidorAgent(MockLLMProvider(), crm))


def _conversar(servico, mensagens):
    lead = servico.obter_ou_criar_lead(None)
    respostas = []
    for msg in mensagens:
        lead = servico.processar_mensagem(lead, msg)
        respostas.append(lead.historico[-1].conteudo)
    return lead, respostas


def test_exemplo_1_compra(tmp_path):
    servico = _servico(tmp_path)
    lead, r = _conversar(servico, [
        "Olá", "Sou Ana Souza, CPF 111.444.777-35",
        "Estou procurando apartamento na zona sul",   # intenção ainda ambígua
        "comprar", "2 quartos", "até 700 mil", "preciso me mudar logo",
        "quero agendar uma visita", "sim",
    ])
    assert "comprar, alugar ou investir" in r[2]            # entende/confirma a intenção
    assert "quartos" in r[3].lower()
    assert "orçamento" in r[4].lower()            # faixa de preço
    assert "urgente" in r[5].lower()              # urgência
    assert "R$" in r[6]                            # sugere imóveis da base
    assert "Esse horário funciona" in r[7] and "confirmada" in r[8]  # encaminha para reunião/visita
    p = lead.perfil
    assert (p.intencao.value, p.regiao_interesse, p.quartos_desejados, p.faixa_preco_max, p.urgencia) == (
        "compra", "Zona Sul", 2, 700000, "imediata")
    assert lead.resumo_corretor  # resumo gerado ao confirmar


def test_exemplo_2_investimento_vai_para_especialista(tmp_path):
    servico = _servico(tmp_path)
    lead, r = _conversar(servico, [
        "Olá", "Sou Carlos Lima, CPF 529.982.247-25",
        "Quero investir em imóveis para renda", "400 mil", "0,6% ao mês",
        "quero agendar uma reunião", "sim",
    ])
    assert "investir" in r[2] and "retorno" in r[3]
    assert "Retorno estimado" in r[4] and "ao mês" in r[4] and "ao ano" in r[4]
    assert "especialista em investimentos" in r[5]
    assert lead.perfil.ticket_investimento == 400000 and lead.perfil.expectativa_retorno
    assert lead.agendamentos[-1].corretor_nome == "Ricardo Moura"
    assert lead.resumo_corretor


def test_resumo_sob_demanda(tmp_path):
    servico = _servico(tmp_path)
    lead, _ = _conversar(servico, ["Olá", "Sou Beto Reis, CPF 123.456.789-09", "quero alugar na zona oeste"])
    assert lead.resumo_corretor is None
    assert servico.gerar_resumo(lead.id)
    assert servico.buscar_lead(lead.id).resumo_corretor
