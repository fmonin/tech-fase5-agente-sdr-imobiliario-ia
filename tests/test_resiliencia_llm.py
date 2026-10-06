"""Se o Azure OpenAI estiver fora do ar, a Área do Corretor não pode quebrar:
a saudação de login cai num texto padrão montado em Python. E o container
respeita o provedor de LLM dos settings que recebe (composition root)."""
import dataclasses

from src.agents.agenda_query_agent import ConsultaAgendaAgent
from src.config import settings
from src.domain.entities import Corretor
from src.infrastructure.llm.factory import criar_llm_provider
from src.infrastructure.llm.mock_provider import MockLLMProvider


class _LLMForaDoAr:
    def gerar_resposta(self, mensagens, temperatura=0.4):
        raise ConnectionError("sem rede")

    def extrair_dados_estruturados(self, texto, schema_descricao):
        raise ConnectionError("sem rede")


class _AgendaVazia:
    def listar_por_corretor(self, corretor_id):
        return []


def test_saudacao_do_corretor_sobrevive_ao_llm_fora_do_ar():
    corretor = Corretor(id="COR001", nome="Ana Paula Souza", telefone="", zonas_atuacao=["Zona Sul"])
    texto = ConsultaAgendaAgent(_LLMForaDoAr(), _AgendaVazia()).saudar(corretor)
    assert texto.startswith("Olá, Ana!")
    assert "menu" in texto


def test_fabrica_respeita_os_settings_recebidos():
    cfg = dataclasses.replace(settings, llm_provider="mock")
    assert isinstance(criar_llm_provider(cfg), MockLLMProvider)
