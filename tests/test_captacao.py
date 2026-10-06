"""Captação: cliente quer VENDER ou colocar para ALUGAR o próprio imóvel."""
from datetime import datetime, timedelta

from src.agents.agendas_cliente import descrever_agendas_do_cliente
from src.infrastructure.crm.mock_crm import MockCRM
from src.services.agenda_service import AgendaService
from tests.test_detalhe_imovel import _servico

CPF = "52998224725"


def _falar(servico, lead, *mensagens):
    for m in mensagens:
        lead = servico.processar_mensagem(lead, m)
    return lead, lead.historico[-1].conteudo


def _cliente(tmp_path):
    servico, agenda = _servico(tmp_path)
    lead = servico.obter_ou_criar_lead(None)
    lead, _ = _falar(servico, lead, "Sou Fernando Monin, CPF 529.982.247-25")
    return servico, agenda, lead


def test_vender_imovel_cadastro_estimativa_e_avaliacao(tmp_path):
    servico, agenda, lead = _cliente(tmp_path)
    lead, texto = _falar(servico, lead, "quero vender meu apartamento na Mooca")
    assert "cadastro" in texto and "Quantos quartos" in texto  # tipo e bairro já aproveitados
    lead, texto = _falar(servico, lead, "2 quartos e 1 banheiro")
    assert "vagas" in texto
    lead, texto = _falar(servico, lead, "1")
    assert "metragem" in texto
    lead, texto = _falar(servico, lead, "65 m2")
    assert "endereço" in texto
    lead, texto = _falar(servico, lead, "Rua da Mooca, 1500")
    assert "complemento" in texto  # bairro veio na 1ª mensagem
    lead, texto = _falar(servico, lead, "apto 52, bloco B")
    assert "Apartamento · 2 quarto(s) · 1 banheiro(s) · 1 vaga(s) · 65 m²" in texto
    assert "Estimativa preliminar" in texto and "avaliação" in texto and "Qual dia e horário" in texto
    assert lead.captacao["etapa"] == "horario"

    dia = (datetime.now() + timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)
    lead, texto = _falar(servico, lead, f"{dia:%d/%m} às 10h")
    assert "Avaliação do imóvel agendada" in texto and "Rua da Mooca, 1500, apto 52, bloco B" in texto
    [av] = [a for a in agenda.listar_por_cliente(CPF) if a.tipo == "avaliacao"]
    assert av.status == "confirmado" and av.data_hora == dia and av.corretor_id in ("COR001", "COR006")
    assert av.imovel_titulo == "Apartamento para venda — Mooca" and "Estimativa" in av.detalhes
    assert lead.captacao == {} and lead.captacoes[0]["endereco"] == "Rua da Mooca, 1500"
    assert any(a.id == av.id for a in lead.agendamentos)

    # Corretor avisado e agenda do cliente mostra a avaliação
    [aviso] = AgendaService(agenda, servico._leads, MockCRM(tmp_path / "crm.json")).avisos_do_cliente_para_corretor(av.corretor_id)
    assert aviso.startswith("🏷️ Nova captação: Fernando Monin")
    assert "Avaliação do imóvel" in descrever_agendas_do_cliente(agenda, None, CPF)


def test_colocar_para_alugar_com_bairro_e_pulos(tmp_path):
    servico, agenda, lead = _cliente(tmp_path)
    lead, texto = _falar(servico, lead, "quero colocar minha casa para alugar")
    assert "Quantos quartos" in texto
    lead, texto = _falar(servico, lead, "3 quartos, 2 banheiros e 2 vagas")
    assert "metragem" in texto
    lead, texto = _falar(servico, lead, "não sei")
    lead, texto = _falar(servico, lead, "Rua das Flores, 10")
    assert "Em qual bairro" in texto
    lead, texto = _falar(servico, lead, "Tatuapé")
    lead, texto = _falar(servico, lead, "não tem")
    assert "para locação" in texto and "Qual dia e horário" in texto
    lead, texto = _falar(servico, lead, "pode ser")  # sem horário: sugere amanhã às 10h
    assert "amanhã às 10h" in texto
    lead, texto = _falar(servico, lead, "sim")
    assert "agendada" in texto
    assert any(a.tipo == "avaliacao" for a in agenda.listar_por_cliente(CPF))


def test_inquilino_e_desistencia_nao_viram_captacao(tmp_path):
    servico, agenda, lead = _cliente(tmp_path)
    lead, texto = _falar(servico, lead, "quero alugar um apartamento perto do meu trabalho")
    assert "cadastro rápido" not in texto and lead.captacao == {}
    lead, texto = _falar(servico, lead, "na verdade quero vender meu apê")
    assert "Quantos quartos" in texto
    lead, texto = _falar(servico, lead, "desisti")
    assert "deixei o cadastro" in texto and lead.captacao == {}


def test_detectar_captacao_e_estimativa(tmp_path):
    from src.agents.captacao_agent import detectar_captacao
    from src.domain.avaliacao_imovel import estimar_valor
    from src.infrastructure.repositories.json_mapa_bairros import carregar_mapa_bairros
    from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository

    assert detectar_captacao("quero vender meu apartamento") == "venda"
    assert detectar_captacao("tenho uma casa na Mooca e quero alugar") == "locacao"
    assert detectar_captacao("quero alugar um apartamento perto do meu trabalho") is None
    assert detectar_captacao("quero comprar um apê") is None

    imoveis = SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json="data/imoveis.json").listar_todos()
    mapa = carregar_mapa_bairros("data/bairros_sp.json")
    e = estimar_valor("venda", "Apartamento", 2, 60, "Mooca", imoveis, mapa)
    assert 400_000 < e.minimo < e.maximo < 700_000 and "em Mooca" in e.referencia
    aluguel = estimar_valor("locacao", "Apartamento", 2, 60, "Tatuapé", imoveis, mapa)
    assert aluguel.maximo < 10_000 and aluguel.texto("locacao").count("/mês") == 2


def test_corretor_registra_captacao_pelo_menu_e_imovel_entra_na_base(tmp_path):
    from types import SimpleNamespace

    from src.agents.menu_corretor_agent import MENU_ABERTO, MenuCorretorAgent, texto_menu
    from src.domain.entities import Corretor
    from src.infrastructure.repositories.json_mapa_bairros import carregar_mapa_bairros
    from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository
    from src.services.captacao_service import CaptacaoService
    from src.services.match_imoveis_service import MatchImoveisService

    servico, agenda, lead = _cliente(tmp_path)
    dia = (datetime.now() + timedelta(days=4)).replace(hour=9, minute=0, second=0, microsecond=0)
    lead, _ = _falar(servico, lead, "quero vender meu apartamento na Mooca", "2 quartos 1 banheiro 1 vaga", "60 m2",
                     "Rua da Mooca, 100", "não tem", f"{dia:%d/%m} às 9h")
    [av] = [a for a in agenda.listar_por_cliente(CPF) if a.tipo == "avaliacao"]
    corretor = Corretor(av.corretor_id, av.corretor_nome, ["Zona Leste"])

    imoveis = SqlitePropertyRepository(caminho_db=tmp_path / "im.db", caminho_seed_json="data/imoveis.json")
    match = MatchImoveisService(imoveis, servico._leads, carregar_mapa_bairros("data/bairros_sp.json"))
    menu = MenuCorretorAgent(SimpleNamespace(), None, None, match, None, CaptacaoService(agenda, servico._leads, match))
    assert "8) 🏷️ Captação de imóveis" in texto_menu("Pedro")

    r = menu.processar(corretor, "8", dict(MENU_ABERTO))
    assert "Suas captações" in r.texto and "Fernando Monin" in r.texto and "Rua da Mooca, 100" in r.texto
    r = menu.processar(corretor, "1", r.pendente)
    assert "Como foi essa avaliação" in r.texto
    r = menu.processar(corretor, "1", r.pendente)  # captado
    assert "valor de anúncio" in r.texto
    r = menu.processar(corretor, "510 mil", r.pendente)
    assert "Imóvel captado e publicado como IM042" in r.texto
    novo = next(i for i in imoveis.listar_todos() if i.id == "IM042")
    assert (novo.tipo_negocio, novo.bairro, novo.preco, novo.quartos, novo.metragem) == ("venda", "Mooca", 510000, 2, 60)
    assert agenda.buscar_por_id(av.id).resultado_visita == "captado"
    assert "Nenhuma captação pendente" in menu.processar(corretor, "captação", dict(MENU_ABERTO)).texto
