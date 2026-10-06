"""Testes da "máquina de estados" do IdentificacaoAgent — o agente que
cadastra/identifica o cliente antes de qualquer qualificação (veja
`src/agents/identification_agent.py`). Não depende de LLM (o agente é
100% determinístico), só de um `ILeadRepository` real (SQLite, banco
temporário) para simular a recuperação de clientes recorrentes."""
import tempfile
from pathlib import Path

import pytest

from src.agents.identification_agent import IdentificacaoAgent
from src.domain.entities import Lead
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository

_CPF_VALIDO = "11144477735"
_CPF_VALIDO_FORMATADO = "111.444.777-35"


@pytest.fixture()
def lead_repository():
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield SqliteLeadRepository(Path(tmp_dir) / "agente_sdr_teste.db")


def _estado_base(mensagem: str, **overrides) -> dict:
    estado = {
        "lead_id": "lead-atual",
        "mensagem_usuario": mensagem,
        "historico_mensagens": [],
        "cliente_cpf": None,
        "cliente_nome": None,
    }
    estado.update(overrides)
    return estado


def test_primeira_mensagem_pergunta_se_e_primeiro_contato(lead_repository):
    agente = IdentificacaoAgent(lead_repository)
    resultado = agente(_estado_base("Oi, quero alugar um apartamento"))

    assert "primeiro contato" in resultado["resposta_agente"].lower() or "antes" in resultado["resposta_agente"].lower()
    assert not resultado.get("cliente_identificado")


def test_cliente_responde_sim_a_ja_falou_antes_e_pedido_so_cpf(lead_repository):
    # A pergunta inicial é "você já falou com a gente antes?" — responder
    # "sim" significa cliente RECORRENTE, então só o CPF deve ser pedido
    # (não nome + CPF). Esse mapeamento já foi invertido por engano numa
    # versão anterior do agente — daí este teste existir isolado.
    agente = IdentificacaoAgent(lead_repository)
    historico = [{"role": "assistant", "content": "você já falou com a gente antes?"}]
    resultado = agente(_estado_base("sim", historico_mensagens=historico))

    resposta = resultado["resposta_agente"].lower()
    assert "cpf" in resposta
    assert "nome" not in resposta
    assert not resultado.get("cliente_identificado")


def test_cliente_responde_nao_a_ja_falou_antes_e_pedido_nome_e_cpf(lead_repository):
    # "não" (não falei com vocês antes) significa cliente NOVO: precisa de
    # nome + CPF, não só CPF.
    agente = IdentificacaoAgent(lead_repository)
    historico = [{"role": "assistant", "content": "você já falou com a gente antes?"}]
    resultado = agente(_estado_base("não, é a primeira vez", historico_mensagens=historico))

    resposta = resultado["resposta_agente"].lower()
    assert "nome" in resposta
    assert "cpf" in resposta
    assert not resultado.get("cliente_identificado")


def test_sim_colado_com_nome_e_cpf_na_mesma_mensagem_nao_vaza_para_o_nome(lead_repository):
    # Bug relatado: "Sim, meu nome é ..., CPF ..." tudo numa linha só. A
    # palavra de confirmação ("sim") não pode virar parte do nome extraído.
    agente = IdentificacaoAgent(lead_repository)
    resultado = agente(
        _estado_base(f"Sim, meu nome é João da Silva, CPF {_CPF_VALIDO_FORMATADO}")
    )

    assert resultado["cliente_identificado"] is True
    assert resultado["cliente_nome"] == "João Da Silva"


def test_cpf_invalido_na_mesma_linha_do_sim_sugere_cpf_de_exemplo(lead_repository):
    # Bug relatado: com um CPF inválido (comum ao testar com números
    # inventados), a mensagem de erro precisa dar uma saída clara — senão
    # o usuário fica preso tentando de novo com outro CPF igualmente
    # inválido, o que parece um "loop".
    agente = IdentificacaoAgent(lead_repository)
    resultado = agente(_estado_base("Sim, meu nome é João da Silva, CPF 123.456.789-00"))

    assert not resultado.get("cliente_identificado")
    resposta = resultado["resposta_agente"].lower()
    assert "não parece válido" in resposta
    assert _CPF_VALIDO_FORMATADO.lower() in resposta


def test_cliente_novo_informa_nome_e_cpf_na_mesma_mensagem(lead_repository):
    agente = IdentificacaoAgent(lead_repository)
    resultado = agente(
        _estado_base(f"Meu nome é Carla Mendes, CPF {_CPF_VALIDO_FORMATADO}")
    )

    assert resultado["cliente_identificado"] is True
    assert resultado["cliente_cpf"] == _CPF_VALIDO
    assert resultado["cliente_nome"] == "Carla Mendes"


def test_cliente_informa_apenas_cpf_e_depois_e_perguntado_o_nome(lead_repository):
    agente = IdentificacaoAgent(lead_repository)
    resultado = agente(_estado_base(_CPF_VALIDO_FORMATADO))

    assert not resultado.get("cliente_identificado")
    assert resultado["cliente_cpf"] == _CPF_VALIDO
    assert "nome" in resultado["resposta_agente"].lower()


def test_cliente_informa_nome_apos_ja_ter_informado_cpf(lead_repository):
    agente = IdentificacaoAgent(lead_repository)
    resultado = agente(
        _estado_base("Carla Mendes", cliente_cpf=_CPF_VALIDO, cliente_nome=None)
    )

    assert resultado["cliente_identificado"] is True
    assert resultado["cliente_cpf"] == _CPF_VALIDO
    assert resultado["cliente_nome"] == "Carla Mendes"


def test_cpf_invalido_pede_para_conferir(lead_repository):
    agente = IdentificacaoAgent(lead_repository)
    resultado = agente(_estado_base("meu cpf é 111.444.777-99"))

    assert not resultado.get("cliente_identificado")
    assert "inválido" in resultado["resposta_agente"].lower() or "válido" in resultado["resposta_agente"].lower()


def test_cliente_recorrente_e_reconhecido_pelo_cpf(lead_repository):
    lead_existente = Lead(nome="Bruno Lima", cpf=_CPF_VALIDO)
    lead_repository.salvar(lead_existente)

    agente = IdentificacaoAgent(lead_repository)
    resultado = agente(_estado_base(f"meu cpf é {_CPF_VALIDO_FORMATADO}"))

    assert resultado["cliente_identificado"] is True
    assert resultado["cliente_cpf"] == _CPF_VALIDO
    assert resultado["cliente_nome"] == "Bruno Lima"
    assert resultado["lead_id_recuperado"] == lead_existente.id
    assert "bruno" in resultado["resposta_agente"].lower()
