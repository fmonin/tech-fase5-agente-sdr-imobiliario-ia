"""Testes de regressão: o agente precisa "lembrar" da conversa ao retomá-la.

Bug relatado: o cliente recorrente informava o CPF, o histórico era
recuperado e exibido na tela, mas ao dizer "quero continuar com a compra do
imóvel" o agente perguntava de novo a região já informada. Causas:
  1. Ao recuperar o lead pelo CPF, o `ConversationService` aplicava sobre
     ele o estado do lead TEMPORÁRIO (perfil vazio), apagando intenção,
     região, quartos etc.
  2. Os agentes só enxergavam a última mensagem — o histórico era montado
     mas nunca enviado para a LLM.
"""
import tempfile
from pathlib import Path

import pytest

from src.agents.clarifier_agent import EsclarecedorAgent
from src.agents.graph import construir_grafo_sdr
from src.agents.qualifier_agent import QualificadorAgent
from src.domain.interfaces import ILLMProvider
from src.infrastructure.crm.mock_crm import MockCRM
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
from src.infrastructure.observability.logger import EventoStore
from src.infrastructure.rag.vector_store import TfidfVectorSearch
from src.infrastructure.repositories.sqlite_corretor_repository import SqliteCorretorRepository
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository
from src.services.conversation_service import ConversationService

CPF_VALIDO = "111.444.777-35"


def _montar_servico(tmp: str) -> ConversationService:
    """Monta o serviço "do zero" apontando para o mesmo banco — simula
    reiniciar a aplicação (nova sessão do cliente)."""
    db = Path(tmp) / "agente_sdr.db"
    imoveis = SqlitePropertyRepository(
        caminho_db=Path(tmp) / "imoveis.db", caminho_seed_json="data/imoveis.json"
    )
    leads = SqliteLeadRepository(db)
    crm = MockCRM(Path(tmp) / "crm.json")
    grafo = construir_grafo_sdr(
        llm_provider=MockLLMProvider(),
        repositorio_imoveis=imoveis,
        busca_semantica=TfidfVectorSearch(imoveis),
        crm=crm,
        lead_repository=leads,
        corretor_repository=SqliteCorretorRepository(
            caminho_db=db, caminho_seed_json="data/corretores.json"
        ),
        agenda_repository=SqliteAgendaRepository(caminho_db=db),
    )
    return ConversationService(grafo, leads, crm, EventoStore(db))


@pytest.fixture()
def tmp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


def _primeira_sessao(tmp: str):
    servico = _montar_servico(tmp)
    lead = servico.obter_ou_criar_lead(None)
    for mensagem in ["Olá", f"Maria Souza {CPF_VALIDO}", "Quero comprar um apartamento na zona sul"]:
        lead = servico.processar_mensagem(lead, mensagem)
    return lead


def test_recuperar_cliente_pelo_cpf_nao_apaga_perfil(tmp_dir):
    lead_original = _primeira_sessao(tmp_dir)
    assert lead_original.perfil.regiao_interesse == "Zona Sul"

    servico = _montar_servico(tmp_dir)
    lead = servico.obter_ou_criar_lead(None)
    for mensagem in ["Oi", "sim", CPF_VALIDO]:
        lead = servico.processar_mensagem(lead, mensagem)

    assert lead.id == lead_original.id
    assert lead.perfil.intencao.value == "compra"
    assert lead.perfil.regiao_interesse == "Zona Sul"


def test_continuar_compra_apos_recuperar_nao_pergunta_regiao_de_novo(tmp_dir):
    _primeira_sessao(tmp_dir)

    servico = _montar_servico(tmp_dir)
    lead = servico.obter_ou_criar_lead(None)
    for mensagem in ["Oi", "sim", CPF_VALIDO, "quero continuar com a compra do imóvel"]:
        lead = servico.processar_mensagem(lead, mensagem)

    resposta = lead.historico[-1].conteudo.lower()
    assert "região" not in resposta
    assert "quartos" in resposta


def test_qualificador_reconstroi_dados_a_partir_do_historico():
    """Mesmo com o perfil vazio, a região dita antes é recuperada do histórico."""
    estado = {
        "lead_id": "x",
        "mensagem_usuario": "quero continuar com a compra do imóvel",
        "historico_mensagens": [
            {"role": "assistant", "content": "Você quer comprar, alugar ou investir?"},
            {"role": "user", "content": "Comprar, algo na zona oeste com 2 quartos"},
            {"role": "assistant", "content": "Qual orçamento você tem em mente?"},
            {"role": "user", "content": "quero continuar com a compra do imóvel"},
        ],
        "intencao": "indefinida",
    }
    resultado = QualificadorAgent(MockLLMProvider())(estado)

    assert resultado["intencao"] == "compra"
    assert resultado["regiao_interesse"] == "Zona Oeste"
    assert resultado["quartos_desejados"] == 2


def test_cpf_no_historico_nao_vira_preco():
    estado = {
        "lead_id": "x",
        "mensagem_usuario": "quero comprar na zona sul",
        "historico_mensagens": [
            {"role": "user", "content": f"Maria Souza {CPF_VALIDO}"},
            {"role": "user", "content": "quero comprar na zona sul"},
        ],
    }
    resultado = QualificadorAgent(MockLLMProvider())(estado)
    assert resultado.get("faixa_preco_max") is None


class _LLMEspiao(ILLMProvider):
    """LLM falsa que registra o que recebeu e devolve strings vazias
    (comportamento comum de LLMs reais para campos ausentes)."""

    def __init__(self) -> None:
        self.textos_extracao: list[str] = []
        self.mensagens_geracao: list[list[dict]] = []

    def gerar_resposta(self, mensagens, temperatura=0.4):
        self.mensagens_geracao.append(mensagens)
        return "ok"

    def extrair_dados_estruturados(self, texto, schema_descricao):
        self.textos_extracao.append(texto)
        return {"intencao": "compra", "regiao_interesse": "", "quartos_desejados": "null"}


def test_extracao_recebe_historico_sem_cpf_e_vazio_nao_apaga_perfil():
    llm = _LLMEspiao()
    estado = {
        "lead_id": "x",
        "mensagem_usuario": "pode ser",
        "historico_mensagens": [
            {"role": "user", "content": f"meu cpf é {CPF_VALIDO}"},
            {"role": "assistant", "content": "Quantos quartos você precisa?"},
            {"role": "user", "content": "pode ser"},
        ],
        "intencao": "compra",
        "regiao_interesse": "Pinheiros",
        "quartos_desejados": 2,
    }
    resultado = QualificadorAgent(llm)(estado)

    texto = llm.textos_extracao[0]
    assert "Quantos quartos você precisa?" in texto
    assert CPF_VALIDO not in texto  # CPF não vai para a LLM
    assert resultado["regiao_interesse"] == "Pinheiros"
    assert resultado["quartos_desejados"] == 2


def test_esclarecedor_envia_historico_para_llm():
    llm = _LLMEspiao()
    estado = {
        "mensagem_usuario": "quero continuar com a compra",
        "historico_mensagens": [
            {"role": "user", "content": "Quero comprar em Pinheiros"},
            {"role": "assistant", "content": "Quantos quartos?"},
            {"role": "user", "content": "quero continuar com a compra"},
        ],
        "intencao": "compra",
        "regiao_interesse": "Pinheiros",
    }
    EsclarecedorAgent(llm)(estado)
    conteudos = [m["content"] for m in llm.mensagens_geracao[0]]

    assert "Quero comprar em Pinheiros" in conteudos
    assert conteudos.count("quero continuar com a compra") == 0  # sem duplicar a atual
    assert "Próximo campo a perguntar: quartos_desejados" in conteudos[-1]


def test_pedido_de_resumo_responde_o_que_ja_conversamos(tmp_dir):
    _primeira_sessao(tmp_dir)
    servico = _montar_servico(tmp_dir)
    lead = servico.obter_ou_criar_lead(None)
    for mensagem in ["Oi", "sim", CPF_VALIDO, "quero que me fale o que conversamos"]:
        lead = servico.processar_mensagem(lead, mensagem)

    resposta = lead.historico[-1].conteudo
    assert "Zona Sul" in resposta
    assert "compra" in resposta.lower()


def test_continuar_nao_dispara_resumo():
    agente = QualificadorAgent(MockLLMProvider())
    resultado = agente({"lead_id": "x", "mensagem_usuario": "quero continuar com a locação que já conversamos"})
    assert resultado["pediu_resumo"] is False
    resultado = agente({"lead_id": "x", "mensagem_usuario": "me fala o que a gente conversou"})
    assert resultado["pediu_resumo"] is True


def test_reconstroi_regiao_dita_ha_muitas_mensagens():
    """Cliente cujo perfil foi apagado pelo bug antigo: a região foi dita há
    mais de 12 mensagens e ainda assim precisa ser recuperada."""
    historico = [{"role": "user", "content": "Apartamento na zona sul"}]
    for i in range(10):
        historico += [{"role": "assistant", "content": f"resposta {i}"}, {"role": "user", "content": "ok"}]
    historico.append({"role": "user", "content": "quero continuar com a locação"})
    estado = {"lead_id": "x", "mensagem_usuario": "quero continuar com a locação",
              "historico_mensagens": historico, "intencao": "aluguel"}

    resultado = QualificadorAgent(MockLLMProvider())(estado)
    assert resultado["regiao_interesse"] == "Zona Sul"


@pytest.mark.parametrize("resposta", ["3", "3 ", "três", "uns 3"])
def test_resposta_curta_com_numero_apos_pergunta_de_quartos(resposta):
    """Bug relatado: o agente perguntava "quantos quartos?", o lead respondia
    só "3" e o agente repetia a pergunta em loop."""
    estado = {
        "lead_id": "x",
        "mensagem_usuario": resposta,
        "historico_mensagens": [
            {"role": "assistant", "content": "Entendido! E quantos quartos você precisa?"},
            {"role": "user", "content": resposta},
        ],
        "intencao": "aluguel",
        "regiao_interesse": "Zona Sul",
        "faixa_preco_max": 3000,  # compra/aluguel agora também exigem orçamento
        "urgencia": "imediata",  # ...e urgência (Exemplo 1 do desafio)
    }
    resultado = QualificadorAgent(MockLLMProvider())(estado)
    assert resultado["quartos_desejados"] == 3
    assert resultado["dados_completos"] is True


def test_numero_solto_sem_pergunta_de_quartos_nao_vira_quartos():
    estado = {
        "lead_id": "x",
        "mensagem_usuario": "3",
        "historico_mensagens": [{"role": "assistant", "content": "Em qual região você procura?"}],
        "intencao": "aluguel",
    }
    assert QualificadorAgent(MockLLMProvider())(estado).get("quartos_desejados") is None
