"""Regressão: loop quando o lead aceita o convite de agendamento só com
"Sim, eu quero" (sem a palavra "agendar"/"visita")."""
import pytest

from src.agents.qualifier_agent import QualificadorAgent
from src.infrastructure.llm.mock_provider import MockLLMProvider

CONVITE = (
    "O garden no Ipiranga tem aluguel estimado de R$ 2.311,20 a R$ 2.824,80. "
    "Podemos agendar uma visita? Quando seria um bom momento para você?"
)
SEM_CONVITE = "Qual região você prefere?"


def _estado(mensagem, ultima_fala_agente=CONVITE):
    return {
        "mensagem_usuario": mensagem,
        "historico_mensagens": [
            {"role": "user", "content": "quero investir 500 mil"},
            {"role": "assistant", "content": ultima_fala_agente},
            {"role": "user", "content": mensagem},
        ],
    }


@pytest.mark.parametrize(
    "mensagem",
    ["Sim eu quero", "sim", "Quero!", "pode ser", "claro", "Bora", "sábado de manhã", "amanhã às 10h"],
)
def test_aceite_do_convite_dispara_agendamento(mensagem):
    assert QualificadorAgent._detectar_quer_agendar(_estado(mensagem)) is True


@pytest.mark.parametrize("mensagem", ["não, obrigado", "agora não", "não quero visitar ainda"])
def test_recusa_nao_agenda(mensagem):
    assert QualificadorAgent._detectar_quer_agendar(_estado(mensagem)) is False


def test_sim_sem_convite_nao_agenda():
    assert QualificadorAgent._detectar_quer_agendar(_estado("sim", SEM_CONVITE)) is False


def test_palavra_de_agendamento_continua_funcionando():
    assert QualificadorAgent._detectar_quer_agendar(_estado("quero marcar uma visita", SEM_CONVITE)) is True


def test_qualificador_completo_roteia_para_agendador():
    from src.agents.graph import _rotear_apos_qualificacao

    estado = _estado("Sim eu quero")
    estado.update({"intencao": "investimento", "ticket_investimento": 500000, "expectativa_retorno": "5% ao mês"})
    atualizacoes = QualificadorAgent(MockLLMProvider())(estado)
    estado.update(atualizacoes)
    assert estado["quer_agendar"] is True
    assert _rotear_apos_qualificacao(estado) == "agendador"


# --- AgendadorAgent: confirmar / trocar horário sem loop -----------------
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from src.agents.intencao_agendamento import (  # noqa: E402
    PERGUNTA_CONFIRMACAO_HORARIO,
    extrair_horario,
)
from src.agents.scheduler_agent import AgendadorAgent  # noqa: E402
from src.infrastructure.crm.mock_crm import MockCRM  # noqa: E402
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository  # noqa: E402
from src.infrastructure.repositories.sqlite_corretor_repository import (  # noqa: E402
    SqliteCorretorRepository,
)


def _agendador(tmp_path):
    db = tmp_path / "agenda.db"
    agenda = SqliteAgendaRepository(caminho_db=db)
    corretores = SqliteCorretorRepository(caminho_db=db, caminho_seed_json=Path("data/corretores.json"))
    return AgendadorAgent(MockLLMProvider(), MockCRM(), corretores, agenda), agenda


def _turno(agente, mensagem, historico):
    historico.append({"role": "user", "content": mensagem})
    estado = {
        "lead_id": "lead-1", "cliente_cpf": "52998224725", "cliente_nome": "Fernando",
        "intencao": "investimento", "regiao_interesse": "Zona Sul",
        "mensagem_usuario": mensagem, "historico_mensagens": list(historico),
    }
    saida = agente(estado)
    historico.append({"role": "assistant", "content": saida["resposta_agente"]})
    return saida


def test_aceite_confirma_o_agendamento_proposto(tmp_path):
    agente, agenda = _agendador(tmp_path)
    historico = [{"role": "assistant", "content": CONVITE}]
    proposta = _turno(agente, "Sim eu quero", historico)
    assert PERGUNTA_CONFIRMACAO_HORARIO in proposta["resposta_agente"]

    confirmacao = _turno(agente, "sim", historico)
    assert confirmacao["agendamento_status"] == "confirmado"
    assert "confirmada" in confirmacao["resposta_agente"]
    assert "?" not in confirmacao["resposta_agente"]  # não convida de novo -> sem loop
    agendamentos = agenda.listar_por_cliente("52998224725")
    assert len(agendamentos) == 1 and agendamentos[0].status == "confirmado"


def test_pedido_de_outro_horario_substitui_a_proposta(tmp_path):
    agente, agenda = _agendador(tmp_path)
    historico = [{"role": "assistant", "content": CONVITE}]
    _turno(agente, "Sim eu quero", historico)
    nova = _turno(agente, "prefiro sábado às 14h", historico)
    assert nova["agendamento_sugerido"].startswith("sábado")
    assert "14h" in nova["agendamento_sugerido"]
    status = sorted(a.status for a in agenda.listar_por_cliente("52998224725"))
    assert status == ["cancelado", "sugerido"]


def test_extrair_horario():
    quinta = datetime(2026, 10, 1, 9, 0)  # quinta-feira
    assert extrair_horario("sábado de manhã", quinta)["data_hora"] == datetime(2026, 10, 3, 10, 0)
    assert extrair_horario("amanhã às 15h", quinta)["data_hora"] == datetime(2026, 10, 2, 15, 0)
    assert extrair_horario("quinta 14:30", quinta)["data_hora"] == datetime(2026, 10, 8, 14, 30)
    assert extrair_horario("sim, eu quero", quinta) is None


@pytest.mark.parametrize("mensagem", ["ok obrigado", "Valeu!", "muito obrigada", "tchau"])
def test_agradecimento_nao_reapresenta_imoveis(mensagem):
    from src.agents.property_agent import ConsultorImoveisAgent

    saida = ConsultorImoveisAgent(MockLLMProvider(), None, None)(
        {"mensagem_usuario": mensagem, "cliente_nome": "Fernando Monin"}
    )
    assert saida["imoveis_sugeridos"] == []
    assert saida["resposta_agente"].startswith("Eu que agradeço, Fernando")
