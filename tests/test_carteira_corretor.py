"""Área do Corretor: clientes da área sem visita e sugestões de imóveis
para levar aos clientes agendados."""
from src.domain.entities import Agendamento, Corretor, IntencaoLead, Lead, RemetenteMensagem
from src.infrastructure.memory.sqlite_agenda_repository import SqliteAgendaRepository
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository
from src.infrastructure.repositories.json_mapa_bairros import carregar_mapa_bairros
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository
from src.services.carteira_corretor_service import CarteiraCorretorService

LESTE = Corretor("COR001", "Pedro Almeida", ["Zona Leste"])
SUL = Corretor("COR002", "Joaquim Ribeiro", ["Zona Sul"])
INVEST = Corretor("COR009", "Ricardo Moura", ["Investimentos"])


def _montar(tmp_path):
    leads = SqliteLeadRepository(tmp_path / "sdr.db")
    agenda = SqliteAgendaRepository(caminho_db=tmp_path / "sdr.db")
    imoveis = SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json="data/imoveis.json")
    servico = CarteiraCorretorService(leads, imoveis, agenda, carregar_mapa_bairros("data/bairros_sp.json"))
    return servico, leads, agenda


def _lead(leads, nome, intencao, regiao=None, **perfil):
    lead = Lead(nome=nome, cpf="52998224725")
    lead.perfil.intencao = intencao
    lead.perfil.regiao_interesse = regiao
    for campo, valor in perfil.items():
        setattr(lead.perfil, campo, valor)
    lead.registrar_mensagem(RemetenteMensagem.LEAD, "oi")
    leads.salvar(lead)
    return lead


def test_clientes_da_area_sem_visita(tmp_path):
    servico, leads, _ = _montar(tmp_path)
    mooca = _lead(leads, "Ana", IntencaoLead.COMPRA, "Mooca", quartos_desejados=2)
    _lead(leads, "Bia", IntencaoLead.ALUGUEL, "Zona Sul")
    _lead(leads, "Caio", IntencaoLead.INVESTIMENTO, "Mooca", ticket_investimento=500000)
    agendado = _lead(leads, "Duda", IntencaoLead.COMPRA, "Tatuapé")
    agendado.agendamentos.append(Agendamento(lead_id=agendado.id, quando_sugerido="sexta", status="confirmado"))
    leads.salvar(agendado)
    anonimo = Lead()  # sem cadastro: não entra
    leads.salvar(anonimo)

    leste = servico.clientes_sem_visita(LESTE)
    assert [c.lead.nome for c in leste] == ["Ana"]  # Mooca -> Zona Leste; Duda já agendou
    assert leste[0].lead.id == mooca.id and "compra" in leste[0].interesse
    assert [c.lead.nome for c in servico.clientes_sem_visita(SUL)] == ["Bia"]
    assert [c.lead.nome for c in servico.clientes_sem_visita(INVEST)] == ["Caio"]


def test_sugestoes_alem_do_imovel_escolhido(tmp_path):
    servico, leads, agenda = _montar(tmp_path)
    lead = _lead(leads, "Fernando", IntencaoLead.COMPRA, "Mooca", quartos_desejados=2, faixa_preco_max=500000)
    lead.registrar_mensagem(RemetenteMensagem.AGENTE, "Opções:\n[[LISTA:IM036,IM016]]")
    leads.salvar(lead)
    agenda.registrar(Agendamento(lead_id=lead.id, quando_sugerido="sexta 14h", status="confirmado", tipo="visita",
                                 cliente_nome="Fernando", corretor_id="COR001", imovel_id="IM036"))

    [item] = servico.sugestoes_para_agendados(LESTE)
    assert item.imovel_escolhido.id == "IM036" and item.intencao == "compra"
    ids = [s.imovel.id for s in item.sugestoes]
    assert ids and "IM036" not in ids
    assert all(s.imovel.tipo_negocio == "venda" for s in item.sugestoes)
    assert all(370000 <= s.imovel.preco <= 620000 for s in item.sugestoes)  # preço próximo do escolhido
    assert not item.sugestoes[0].ja_mostrado_ao_cliente  # novidades primeiro
    assert item.sugestoes[0].imovel.bairro == "Mooca" and "mesmo bairro" in item.sugestoes[0].motivo
    assert servico.sugestoes_para_agendados(SUL) == []


def test_imoveis_da_area_do_corretor_e_menu(tmp_path):
    from types import SimpleNamespace

    from src.agents.menu_corretor_agent import MENU_ABERTO, MenuCorretorAgent, texto_menu

    servico, _, _ = _montar(tmp_path)
    leste = servico.imoveis_da_area(LESTE)
    assert leste and all(im.zona == "Zona Leste" for im in leste)
    assert all(im.tipo_negocio == "aluguel" and im.bairro == "Mooca"
               for im in servico.imoveis_da_area(LESTE, "aluguel", "Mooca"))
    assert all(im.finalidade_investimento for im in servico.imoveis_da_area(INVEST))

    menu = MenuCorretorAgent(SimpleNamespace(), servico, None, None, None)
    assert "9) 🏘️ Imóveis da minha área" in texto_menu("Pedro")
    r = menu.processar(LESTE, "9", dict(MENU_ABERTO))
    assert f"{len(leste)} imóvel(is) na sua área** (Zona Leste)" in r.texto and "IM0" in r.texto
    r = menu.processar(LESTE, "quero ver os imóveis da minha área para alugar na Mooca", dict(MENU_ABERTO))
    assert "(Zona Leste · para aluguel · Mooca)" in r.texto and "0 à venda" in r.texto
