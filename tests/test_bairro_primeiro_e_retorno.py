"""Pedido do usuário (04/10): aluguel na Mooca mostrava Santana. Agora:
1º o bairro pedido (inclusive com valores próximos), e os bairros vizinhos só
se o lead quiser; para investimento, sugere onde o RETORNO é maior."""
from tests.test_cenarios_desafio import _conversar, _servico
from src.agents.property_agent import ConsultorImoveisAgent
from src.infrastructure.llm.mock_provider import MockLLMProvider
from src.infrastructure.mercado.json_market_data_repository import JsonMarketDataRepository
from src.infrastructure.rag.vector_store import TfidfVectorSearch
from src.infrastructure.repositories.json_mapa_bairros import carregar_mapa_bairros
from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository


def _agente(tmp_path):
    repo = SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json="data/imoveis.json")
    return ConsultorImoveisAgent(
        MockLLMProvider(), repo, TfidfVectorSearch(repo),
        JsonMarketDataRepository("data/mercado_investimento.json"), carregar_mapa_bairros("data/bairros_sp.json"),
    )


def _estado(**kw):
    return {"mensagem_usuario": "x", "historico_mensagens": [], **kw}


def test_aluguel_mooca_mostra_primeiro_a_mooca_com_valor_proximo(tmp_path):
    agente = _agente(tmp_path)
    estado = _estado(intencao="aluguel", regiao_interesse="Mooca", quartos_desejados=3, faixa_preco_max=3500)
    sugeridos = agente._buscar_imoveis(estado)
    assert {im.bairro for im in sugeridos} == {"Mooca"}          # nada de Santana
    assert sugeridos[0].quartos == 3 and sugeridos[0].preco == 3900  # 3 quartos, valor mais próximo
    assert "Tatuapé" in agente._oferecer_vizinhos                 # vizinhos ficam como próxima opção
    saida = agente(estado)
    assert "Quer que eu te mostre as opções nos bairros vizinhos?" in saida["resposta_agente"]


def test_sim_aos_vizinhos_mostra_tatuape_e_belem_dentro_do_orcamento(tmp_path):
    agente = _agente(tmp_path)
    estado = _estado(intencao="aluguel", regiao_interesse="Mooca", quartos_desejados=3,
                     faixa_preco_max=3500, ampliar_busca=True)
    sugeridos = agente._buscar_imoveis(estado)
    assert {im.bairro for im in sugeridos} <= {"Tatuapé", "Belém", "Água Rasa", "Brás", "Vila Prudente", "Ipiranga", "Cambuci"}
    assert all(im.preco <= 3500 and im.quartos >= 3 for im in sugeridos[:2])


def test_conversa_aluguel_mooca_depois_vizinhos(tmp_path):
    servico = _servico(tmp_path)
    lead, r = _conversar(servico, [
        "Olá", "Sou Fernando Monin, CPF 529.982.247-25", "Quero alugar", "Mooca", "3", "3500", "Ano que vem",
    ])
    assert "Santana" not in r[-1] and "Mooca" in r[-1]
    lead = servico.processar_mensagem(lead, "sim")
    assert "Tatuapé" in lead.historico[-1].conteudo or "Belém" in lead.historico[-1].conteudo
    assert not lead.agendamentos  # "sim" aos vizinhos não é "sim" para agendar


def test_investimento_sugere_onde_o_retorno_e_maior(tmp_path):
    agente = _agente(tmp_path)
    # Pediu Tatuapé (aluguel de mercado R$ 44/m², m² de R$ 10.253): a Mooca vizinha rende mais
    estado = _estado(intencao="investimento", regiao_interesse="Tatuapé", ticket_investimento=500000,
                     expectativa_retorno="0,6% ao mês")
    sugeridos = agente._buscar_imoveis(estado)
    assert sugeridos[0].bairro == "Tatuapé"                      # 1º a melhor opção no bairro pedido
    maiores = [im for im in sugeridos[1:] if "MAIOR" in agente._observacoes[im.id]]
    assert maiores and all(im.bairro == "Mooca" for im in maiores)
    lista = agente._montar_lista(estado, sugeridos)
    assert "VivaReal/ZAP" in lista and "FipeZAP" in lista       # números com fonte de mercado


def test_investimento_na_mooca_ja_e_o_melhor_retorno(tmp_path):
    agente = _agente(tmp_path)
    estado = _estado(intencao="investimento", regiao_interesse="Mooca", ticket_investimento=500000)
    sugeridos = agente._buscar_imoveis(estado)
    assert sugeridos and all(im.bairro == "Mooca" for im in sugeridos)  # nada rende mais por perto


def test_lista_resumida_por_tipo_de_busca(tmp_path):
    from src.agents.apresentacao_imoveis import resumo_imovel
    repo = SqlitePropertyRepository(caminho_db=tmp_path / "i.db", caminho_seed_json="data/imoveis.json")
    por_id = {im.id: im for im in repo.listar_todos()}
    invest = resumo_imovel(por_id["IM036"], "investimento")
    assert invest.startswith("Apartamento · 2 quartos") and "1 vaga" in invest and "Mooca" in invest
    assert "Retorno estimado" in invest and "ao mês" in invest and "ao ano" in invest
    aluguel = resumo_imovel(por_id["IM031"], "aluguel")
    assert "3 quartos (1 suíte)" in aluguel and "Aluguel: R$ 3.900,00/mês" in aluguel and "Condomínio" in aluguel
    compra = resumo_imovel(por_id["IM038"], "compra")
    assert "Valor de venda: R$ 790.000,00" in compra and "2 vagas" in compra


def test_zona_com_letra_minuscula_nao_vira_outra_regiao(tmp_path):
    """Telegram 04/10: "500.000,00 Na zona Leste" -> o agente disse que não havia
    nada na Zona Leste e ofereceu... bairros da Zona Leste."""
    agente = _agente(tmp_path)
    estado = _estado(intencao="investimento", regiao_interesse="zona Leste", ticket_investimento=500000,
                     expectativa_retorno="1,5% ao mês")
    sugeridos = agente._buscar_imoveis(estado)
    assert sugeridos and all(im.zona == "Zona Leste" for im in sugeridos)
    assert agente._info_regiao == ""
    assert all("região pedida (Zona Leste)" in agente._observacoes[im.id] for im in sugeridos)


def test_escolha_pelo_numero_da_lista(tmp_path):
    servico = _servico(tmp_path)
    lead, r = _conversar(servico, [
        "Olá", "Sou Fernando Monin, CPF 529.982.247-25", "Quero alugar", "Mooca", "3", "3500", "Ano que vem",
    ])
    ids = r[-1].split("[[LISTA:")[1].rstrip("]").split(",")
    lead = servico.processar_mensagem(lead, "fotos do 2")
    assert f"[[FOTOS:{ids[1]}]]" in lead.historico[-1].conteudo
    assert lead.perfil.quartos_desejados == 3  # "2" não virou quantidade de quartos
