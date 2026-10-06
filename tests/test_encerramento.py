"""O cliente encerra o atendimento quando quiser (com resumo, nota e sem follow-up)."""
from datetime import datetime, timedelta

from src.agents.encerramento_agent import nota_do_atendimento, quer_encerrar
from src.agents.followup_agent import FollowUpAgent
from src.infrastructure.llm.mock_provider import MockLLMProvider
from tests.test_detalhe_imovel import _servico


def _falar(servico, lead, *mensagens):
    for m in mensagens:
        lead = servico.processar_mensagem(lead, m)
    return lead, lead.historico[-1].conteudo


def test_frases_de_encerramento():
    for t in ("quero encerrar o atendimento", "pode finalizar", "Tchau!", "/encerrar", "por hoje é só, obrigado",
              "encerrar a conversa"):
        assert quer_encerrar(t), t
    for t in ("quero alugar um apê", "obrigado", "quero fechar negócio", "cancela minha visita", "tchau e bença da Mooca?"):
        assert not quer_encerrar(t), t
    assert nota_do_atendimento("5") == 5 and nota_do_atendimento("nota 4") == 4 and nota_do_atendimento("7") is None


def _agente_followup(servico):
    from src.infrastructure.repositories.json_mapa_bairros import carregar_mapa_bairros
    from src.infrastructure.repositories.sqlite_property_repository import SqlitePropertyRepository
    from src.services.argumentos_followup import ArgumentosFollowUp
    from src.services.carteira_corretor_service import CarteiraCorretorService
    from src.services.match_imoveis_service import MatchImoveisService

    leads, mapa = servico._leads, carregar_mapa_bairros("data/bairros_sp.json")
    imoveis = SqlitePropertyRepository(caminho_db=leads._caminho.parent / "im.db", caminho_seed_json="data/imoveis.json")
    match = MatchImoveisService(imoveis, leads, mapa)
    carteira = CarteiraCorretorService(leads, imoveis, None, mapa)
    return FollowUpAgent(MockLLMProvider(), leads, argumentos=ArgumentosFollowUp(imoveis, leads, match, carteira))


def _inativo(servico, lead):
    lead = servico._leads.buscar_por_id(lead.id)
    lead.aguardando_resposta_desde = datetime.utcnow() - timedelta(hours=3)
    servico._leads.salvar(lead)


def test_encerrar_com_resumo_nota_e_reabrir(tmp_path):
    servico, _ = _servico(tmp_path)
    lead = servico.obter_ou_criar_lead(None)
    lead, _ = _falar(servico, lead, "Sou Fernando Monin, CPF 529.982.247-25", "quero alugar na Mooca",
                     "quero vender meu apartamento")  # cadastro de captação pela metade
    lead, texto = _falar(servico, lead, "pode encerrar o atendimento")
    assert texto.startswith("Atendimento encerrado ✅ Foi um prazer te ajudar, Fernando!")
    assert "cadastro do seu imóvel ficou salvo" in texto and "de 1 a 5" in texto
    assert lead.atendimento_encerrado and lead.captacao  # pendência guardada para o follow-up

    lead, texto = _falar(servico, lead, "5")
    assert "Obrigado pela avaliação" in texto and lead.notas_atendimento == [5] and lead.atendimento_encerrado
    lead, texto = _falar(servico, lead, "quero ver apartamentos para alugar na Mooca")
    assert not lead.atendimento_encerrado and "Obrigado pela avaliação" not in texto


def test_followup_insiste_no_negocio_pendente_mesmo_encerrado(tmp_path):
    servico, _ = _servico(tmp_path)
    lead = servico.obter_ou_criar_lead(None)
    lead, _ = _falar(servico, lead, "Sou Fernando Monin, CPF 529.982.247-25", "quero alugar na Mooca",
                     "2 quartos", "até 3.500", "tchau")
    assert lead.atendimento_encerrado
    _inativo(servico, lead)
    agente = _agente_followup(servico)
    [enviado] = agente.executar_para_leads_inativos(60)
    assert "imóvel(is) disponível(is) combinam" in enviado.mensagem and "Vamos retomar" in enviado.mensagem
    for _ in range(5):
        _inativo(servico, lead)
        agente.executar_para_leads_inativos(60)
    assert servico._leads.buscar_por_id(lead.id).followups_enviados == 3  # insiste, mas com limite


def test_sem_pendencia_ou_pedido_para_parar_nao_recebe_followup(tmp_path):
    servico, _ = _servico(tmp_path)
    agente = _agente_followup(servico)
    # Encerrou sem nada pela metade (nem intenção definida)
    lead = servico.obter_ou_criar_lead(None)
    lead, _ = _falar(servico, lead, "Sou Fernando Monin, CPF 529.982.247-25", "encerrar atendimento")
    _inativo(servico, lead)
    assert agente.executar_para_leads_inativos(60) == []
    # Pediu para não receber mensagens, mesmo com negócio pendente
    lead, _ = _falar(servico, lead, "quero comprar na Mooca")
    lead, texto = _falar(servico, lead, "por favor, pare de me mandar mensagens")
    assert "Não vou mais te enviar mensagens" in texto and lead.nao_contatar
    _inativo(servico, lead)
    assert agente.executar_para_leads_inativos(60) == []
