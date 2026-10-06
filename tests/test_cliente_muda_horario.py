"""Regressão (03/10, Telegram e web): o cliente não conseguia mudar a data.
- "Quero dia 8 desse mes as 13hrs" não era entendido (formato "dia 8"/"13hrs")
  e caía no Consultor, que "prometia" o horário sem agendar;
- "Quero mudar o horário" era lido como "sim" e CONFIRMAVA o horário antigo."""
from datetime import datetime

import pytest

from src.agents.intencao_agendamento import eh_aceite, eh_pedido_de_mudanca, extrair_horario
from tests.test_detalhe_imovel import _servico

AGORA = datetime(2026, 10, 3, 15, 0)


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Quero dia 8 desse mes as 13hrs", datetime(2026, 10, 8, 13, 0)),
        ("08/10 às 9", datetime(2026, 10, 8, 9, 0)),
        ("8 de outubro, 13 horas", datetime(2026, 10, 8, 13, 0)),
        ("dia 2 às 14h", datetime(2026, 11, 2, 14, 0)),  # dia 2 já passou -> mês que vem
        ("sexta 14:30", datetime(2026, 10, 9, 14, 30)),
        ("pode ser às 16", datetime(2026, 10, 4, 16, 0)),
    ],
)
def test_extrai_datas_e_horas_variadas(texto, esperado):
    assert extrair_horario(texto, AGORA)["data_hora"] == esperado


@pytest.mark.parametrize("texto", ["Quero mudar o horário", "outro dia", "não posso nesse horário", "prefiro outra data"])
def test_pedido_de_mudanca_nao_e_aceite(texto):
    assert eh_pedido_de_mudanca(texto) and not eh_aceite(texto)


def test_cliente_muda_o_horario_proposto(tmp_path):
    servico, agenda = _servico(tmp_path)
    lead = servico.obter_ou_criar_lead(None)
    for msg in ("Sou Fernando Monin, CPF 529.982.247-25", "quero comprar na zona leste, mooca",
                "2 quartos", "até 400 mil", "quero agendar uma visita"):
        lead = servico.processar_mensagem(lead, msg)
    assert "Esse horário funciona para você?" in lead.historico[-1].conteudo

    lead = servico.processar_mensagem(lead, "Quero mudar o horário")
    assert "Para qual dia e horário você prefere?" in lead.historico[-1].conteudo
    assert all(a.status != "confirmado" for a in agenda.listar_por_cliente("52998224725"))

    lead = servico.processar_mensagem(lead, "Quero dia 8 desse mes as 13hrs")
    resposta = lead.historico[-1].conteudo
    assert "às 13h" in resposta and "Esse horário funciona para você?" in resposta

    lead = servico.processar_mensagem(lead, "sim")
    confirmados = [a for a in agenda.listar_por_cliente("52998224725") if a.status == "confirmado"]
    assert len(confirmados) == 1 and confirmados[0].data_hora.hour == 13
    assert sum(a.status == "cancelado" for a in agenda.listar_por_cliente("52998224725")) == 1


def test_horario_dito_antes_e_usado_ao_pedir_para_agendar(tmp_path):
    servico, agenda = _servico(tmp_path)
    lead = servico.obter_ou_criar_lead(None)
    for msg in ("Sou Fernando Monin, CPF 529.982.247-25", "quero comprar na zona leste, mooca",
                "2 quartos", "até 400 mil", "dá para ver na sexta às 16h?", "quero agendar"):
        lead = servico.processar_mensagem(lead, msg)
    proposta = agenda.listar_por_cliente("52998224725")[-1]
    assert proposta.data_hora.weekday() == 4 and proposta.data_hora.hour == 16


def test_erro_de_digitacao_na_data(tmp_path):
    """Telegram 03/10: "Quero fia 8 desse mes" caía nas sugestões de imóveis."""
    assert extrair_horario("Quero fia 8 desse mes", AGORA)["data_hora"].day == 8

    servico, agenda = _servico(tmp_path)
    lead = servico.obter_ou_criar_lead(None)
    for msg in ("Sou Fernando Monin, CPF 529.982.247-25", "quero comprar na zona leste, mooca",
                "2 quartos", "até 400 mil", "quero agendar uma visita"):
        lead = servico.processar_mensagem(lead, msg)

    lead = servico.processar_mensagem(lead, "Quero fia 8 desse mes")
    assert "Esse horário funciona para você?" in lead.historico[-1].conteudo
    assert agenda.listar_por_cliente("52998224725")[-1].data_hora.day == 8

    # Resposta incompreensível com proposta aberta: o Agendador pergunta de novo
    lead = servico.processar_mensagem(lead, "hmm talvez")
    assert "Para qual dia e horário você prefere?" in lead.historico[-1].conteudo

    # Assunto novo com proposta aberta: sai do agendamento normalmente
    lead = servico.processar_mensagem(lead, "quero ver mais fotos do imóvel da Mooca")
    assert "Para qual dia e horário" not in lead.historico[-1].conteudo
