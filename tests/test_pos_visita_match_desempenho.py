"""Área do Corretor: resultado das visitas (funil), pós-visita automático,
match de imóveis novos × clientes, painel de desempenho e menu do chat."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from src.agents.menu_corretor_agent import MENU_ABERTO, MenuCorretorAgent, texto_menu
from src.domain.entities import Agendamento, Corretor, Imovel, IntencaoLead
from src.domain.feedback_visita import aplicar_feedback, classificar_feedback
from src.infrastructure.repositories.json_mapa_bairros import carregar_mapa_bairros
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository
from src.services.carteira_corretor_service import CarteiraCorretorService
from src.services.desempenho_corretor_service import DesempenhoCorretorService
from src.services.match_imoveis_service import MatchImoveisService
from src.services.pos_visita_service import PosVisitaService
from tests.test_detalhe_imovel import _servico

PEDRO = Corretor("COR001", "Pedro Almeida", ["Zona Leste"])
CPF = "52998224725"


def _ambiente(tmp_path):
    servico, agenda = _servico(tmp_path)
    leads = servico._leads
    imoveis = SqlitePropertyRepository(caminho_db=tmp_path / "imoveis.db", caminho_seed_json="data/imoveis.json")
    mapa = carregar_mapa_bairros("data/bairros_sp.json")
    eventos = []
    obs = SimpleNamespace(registrar_evento=lambda nome, dados: eventos.append(nome))
    pos = PosVisitaService(agenda, leads, imoveis, None, obs)
    carteira = CarteiraCorretorService(leads, imoveis, agenda, mapa)
    match = MatchImoveisService(imoveis, leads, mapa, observador=obs)
    desempenho = DesempenhoCorretorService(agenda, carteira)
    lead = servico.obter_ou_criar_lead(None)
    lead = servico.processar_mensagem(lead, "Sou Fernando Monin, CPF 529.982.247-25")
    lead.perfil.intencao = IntencaoLead.COMPRA
    lead.perfil.regiao_interesse = "Mooca"
    lead.perfil.quartos_desejados = 2
    lead.perfil.faixa_preco_max = 500000
    leads.salvar(lead)
    return SimpleNamespace(servico=servico, agenda=agenda, leads=leads, imoveis=imoveis, pos=pos,
                           carteira=carteira, match=match, desempenho=desempenho, lead=lead, eventos=eventos)


def _visita(amb, dias=-1, imovel_id="IM036", titulo="Apartamento na Mooca"):
    a = Agendamento(lead_id=amb.lead.id, cliente_cpf=CPF, cliente_nome="Fernando Monin", tipo="visita",
                    status="confirmado", quando_sugerido="x", corretor_id="COR001", corretor_nome="Pedro Almeida",
                    data_hora=(datetime.now() + timedelta(days=dias)).replace(microsecond=0),
                    imovel_id=imovel_id, imovel_titulo=titulo)
    amb.agenda.registrar(a)
    return a


def test_classificar_feedback():
    assert classificar_feedback("Adorei o apartamento!") == ("positivo", None)
    assert classificar_feedback("achei caro demais") == ("negativo", "Preço")
    assert classificar_feedback("não gostei, muito pequeno") == ("negativo", "Tamanho")
    assert classificar_feedback("o condomínio é caro") == ("negativo", "Condomínio caro")
    assert classificar_feedback("ok") == ("neutro", None)


def test_resultado_da_visita_alimenta_funil_e_proximas_sugestoes(tmp_path):
    amb = _ambiente(tmp_path)
    a = _visita(amb)
    assert [v.id for v in amb.pos.visitas_para_registrar("COR001")] == [a.id]
    ok, texto = amb.pos.registrar_resultado(a.id, "COR001", "nao_gostou", "Preço", "achou o valor alto")
    assert ok and "Preço" in texto and amb.pos.visitas_para_registrar("COR001") == []
    lead = amb.leads.buscar_por_id(amb.lead.id)
    assert lead.feedback_visitas[0]["imovel_id"] == "IM036" and lead.feedback_visitas[0]["motivo"] == "Preço"

    # Próximas sugestões: sem o IM036 e só os mais baratos que ele (R$ 495 mil)
    todos = amb.imoveis.listar_todos()
    sugeridos, motivos = aplicar_feedback([im for im in todos if im.bairro == "Mooca"], lead.feedback_visitas)
    assert sugeridos and all(im.id != "IM036" and im.preco < 495000 for im in sugeridos) and motivos == ["Preço"]

    b = _visita(amb, dias=-2, imovel_id="IM039", titulo="Outro")
    amb.pos.registrar_resultado(b.id, "COR001", "fechado")
    d = amb.desempenho.calcular(PEDRO)
    assert (d.visitas_agendadas, d.visitas_realizadas, d.propostas, d.fechados) == (2, 2, 1, 1)
    assert d.taxa_fechamento == 0.5 and d.motivos_perda == {"Preço": 1}
    assert "Negócios fechados: 1" in d.resumo_texto()
    assert "resultado_visita_registrado" in amb.eventos


def test_pos_visita_pergunta_ao_cliente_e_avisa_o_corretor(tmp_path):
    from src.infrastructure.crm.mock_crm import MockCRM
    from src.services.agenda_service import AgendaService

    amb = _ambiente(tmp_path)
    a = _visita(amb)
    _visita(amb, dias=+3)  # futura: ainda não pergunta
    [enviado] = amb.pos.enviar_pos_visita(horas_apos=0)
    assert "Como foi a visita ao Apartamento na Mooca" in enviado.mensagem and enviado.agendamento.id == a.id
    assert amb.pos.enviar_pos_visita(horas_apos=0) == []  # não repete

    lead = amb.servico.obter_ou_criar_lead(amb.lead.id)
    assert "Como foi a visita" in lead.historico[-1].conteudo
    lead = amb.servico.processar_mensagem(lead, "Gostei da sala, mas achei caro")
    assert "Já passei" in lead.historico[-1].conteudo
    salvo = amb.agenda.buscar_por_id(a.id)
    assert salvo.feedback_cliente == "Gostei da sala, mas achei caro"

    aviso = AgendaService(amb.agenda, amb.leads, MockCRM(tmp_path / "crm.json")).avisos_do_cliente_para_corretor("COR001")
    assert aviso and aviso[0].startswith("💬 Fernando Monin sobre a visita")

    # resposta negativa: o motivo vai para as próximas sugestões
    [b] = [x for x in amb.pos.enviar_pos_visita(lead_id=amb.lead.id)]
    lead = amb.servico.obter_ou_criar_lead(amb.lead.id)
    lead = amb.servico.processar_mensagem(lead, "não gostei, muito pequeno")
    assert "outras opções" in lead.historico[-1].conteudo
    assert any(f["motivo"] == "Tamanho" for f in amb.leads.buscar_por_id(amb.lead.id).feedback_visitas)


def test_match_de_imovel_novo_com_cliente_e_aviso(tmp_path):
    amb = _ambiente(tmp_path)
    novo = Imovel(id="", titulo="Apê novo na Mooca", tipo_negocio="venda", finalidade_investimento=False, zona="",
                  bairro="mooca", preco=450000, quartos=2, metragem=58, descricao="Reformado")
    item = amb.match.cadastrar(novo, PEDRO)
    assert item.imovel.id == "IM042" and item.imovel.zona == "Zona Leste" and item.imovel.bairro == "Mooca"
    [cliente] = item.clientes
    assert cliente.lead.id == amb.lead.id and cliente.aderencia == 3 and "mesmo bairro" in cliente.motivo

    caro = amb.match.cadastrar(Imovel(id="", titulo="Cobertura", tipo_negocio="venda", finalidade_investimento=False,
                                      zona="", bairro="Mooca", preco=900000, quartos=3, metragem=120, descricao="x"), PEDRO)
    assert caro.clientes == []  # fora do orçamento

    novos = amb.match.imoveis_novos_com_match(PEDRO)
    assert {i.imovel.id for i in novos} == {"IM042", "IM043"}
    texto = amb.match.avisar_cliente(amb.lead.id, "IM042", PEDRO)
    assert "Acabou de entrar um imóvel" in texto and "[[LISTA:IM042]]" in texto
    lead = amb.leads.buscar_por_id(amb.lead.id)
    assert lead.historico[-1].conteudo == texto and "IM042" in lead.imoveis_avisados
    assert amb.match.clientes_para_imovel(item.imovel)[0].ja_avisado
    assert "match_avisado" in amb.eventos


def test_menu_do_corretor_e_registro_pelo_chat(tmp_path):
    amb = _ambiente(tmp_path)
    a = _visita(amb)
    agenda_query = SimpleNamespace(responder=lambda corretor, pergunta: "Sua agenda: 1 visita.")
    menu = MenuCorretorAgent(agenda_query, amb.carteira, amb.pos, amb.match, amb.desempenho)
    texto = texto_menu("Pedro Almeida")
    assert texto.startswith("O que deseja fazer, Pedro?") and "2) 📝 Registrar resultado de visita / negociação" in texto

    assert menu.processar(PEDRO, "menu").texto == texto
    assert menu.processar(PEDRO, "1", dict(MENU_ABERTO)).texto.startswith("Sua agenda")
    assert menu.processar(PEDRO, "quero cancelar a visita da Ana", dict(MENU_ABERTO)) is None  # vai p/ gestão

    r = menu.processar(PEDRO, "2", dict(MENU_ABERTO))
    assert "De qual visita" in r.texto and r.pendente["etapa"] == "visita"
    r = menu.processar(PEDRO, "1", r.pendente)
    assert "Como foi essa visita" in r.texto
    r = menu.processar(PEDRO, "4", r.pendente)  # não gostou
    assert "principal motivo" in r.texto
    r = menu.processar(PEDRO, "localização", r.pendente)
    assert "Resultado registrado" in r.texto and "Localização" in r.texto
    assert amb.agenda.buscar_por_id(a.id).resultado_visita == "nao_gostou"

    assert "Seu desempenho" in menu.processar(PEDRO, "6", dict(MENU_ABERTO)).texto
    assert "Nenhum imóvel novo" in menu.processar(PEDRO, "5", dict(MENU_ABERTO)).texto


def test_proposta_aceita_vira_negocio_fechado_no_desempenho(tmp_path):
    amb = _ambiente(tmp_path)
    a = _visita(amb)
    agenda_query = SimpleNamespace(responder=lambda corretor, pergunta: "")
    menu = MenuCorretorAgent(agenda_query, amb.carteira, amb.pos, amb.match, amb.desempenho)

    # 1º: fez proposta de 480 mil
    r = menu.processar(PEDRO, "registrar visita", dict(MENU_ABERTO))
    r = menu.processar(PEDRO, "1", r.pendente)
    r = menu.processar(PEDRO, "2", r.pendente)
    assert "valor da proposta" in r.texto
    r = menu.processar(PEDRO, "480 mil", r.pendente)
    assert "Fez proposta" in r.texto and "R$ 480.000,00" in r.texto
    assert [n.id for n in amb.pos.negociacoes_em_andamento("COR001")] == [a.id]  # continua na lista
    assert amb.desempenho.calcular(PEDRO).propostas_em_aberto == 1

    # 2º: proposta aceita -> "fechei" leva ao mesmo fluxo, marca fechado
    r = menu.processar(PEDRO, "fechei o negócio da Mooca", dict(MENU_ABERTO))
    assert "em negociação" in r.texto
    r = menu.processar(PEDRO, "1", r.pendente)
    r = menu.processar(PEDRO, "3", r.pendente)
    r = menu.processar(PEDRO, "475 mil", r.pendente)
    assert "Negócio fechado" in r.texto and "Parabéns" in r.texto
    assert amb.pos.negociacoes_em_andamento("COR001") == []
    d = amb.desempenho.calcular(PEDRO)
    assert (d.fechados, d.propostas, d.propostas_em_aberto, d.valor_fechado) == (1, 1, 0, 475000)
    assert "R$ 475.000,00" in d.resumo_texto()
