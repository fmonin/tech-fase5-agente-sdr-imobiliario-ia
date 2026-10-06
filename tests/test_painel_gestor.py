"""Painel da Imobiliária (visão do gestor)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.domain.entities import IntencaoLead, TemperaturaLead
from src.infrastructure.repositories.sqlite_corretor_repository import SqliteCorretorRepository
from src.services.painel_gestor_service import PainelGestorService
from tests.test_pos_visita_match_desempenho import _ambiente, _visita


def _painel(tmp_path):
    from pathlib import Path

    amb = _ambiente(tmp_path)
    corretores = SqliteCorretorRepository(caminho_db=tmp_path / "sdr.db", caminho_seed_json=Path("data/corretores.json"))
    eventos = SimpleNamespace(listar_eventos=lambda limite: [])
    painel = PainelGestorService(amb.leads, amb.agenda, corretores, amb.imoveis, amb.carteira, amb.match,
                                 amb.desempenho, eventos, horas_lead_quente=0)
    return amb, painel


def _lead(amb, nome, cpf, intencao, regiao, **perfil):
    lead = amb.servico.obter_ou_criar_lead(None)
    lead = amb.servico.processar_mensagem(lead, f"Sou {nome}, CPF {cpf}")
    lead.perfil.intencao = intencao
    lead.perfil.regiao_interesse = regiao
    for k, v in perfil.items():
        setattr(lead.perfil, k, v)
    amb.leads.salvar(lead)
    return lead


def test_painel_do_gestor(tmp_path):
    amb, painel = _painel(tmp_path)
    a = _visita(amb)  # Fernando: compra Mooca, visita realizada ontem
    amb.pos.registrar_resultado(a.id, "COR001", "fechado", valor=480000)
    _lead(amb, "Ana Lima", "111.444.777-35", IntencaoLead.ALUGUEL, "Mooca", quartos_desejados=5,
          faixa_preco_max=1500, temperatura=TemperaturaLead.QUENTE)  # quer algo que não existe

    p = painel.calcular()
    assert p.leads_novos == 2 and p.valor_fechado == 480000
    assert dict(p.funil)["Negócios fechados"] == 1 and dict(p.funil)["Com visita agendada"] == 1
    assert p.taxa_fechamento == 1.0 and p.tempo_primeira_resposta_s is not None

    pedro = next(l for l in p.corretores if l.corretor.id == "COR001")
    assert pedro.fechados == 1 and pedro.valor_fechado == 480000 and p.corretores[0] is pedro  # ranking

    falta = next(d for d in p.demanda if d.intencao == "aluguel")
    assert (falta.regiao, falta.clientes, falta.clientes_sem_opcao) == ("Mooca", 1, 1)
    assert p.demanda[0] is falta  # falta de imóvel aparece primeiro

    tipos = {i.tipo for i in p.atencao}
    assert "🔥 Lead quente sem visita" in tipos
    ana = next(l for l in p.leads if l["Cliente"] == "Ana Lima")
    fernando = next(l for l in p.leads if l["Cliente"] == "Fernando Monin")
    assert fernando["Etapa"] == "Negócio fechado" and fernando["Corretor"] == "Pedro Almeida"
    assert ana["Etapa"] == "Qualificado" and "Pedro Almeida" in ana["Corretor"]
    assert p.ia["conversas"] == 2 and p.ia["canais"] == {"web": 2}

    b = _visita(amb, dias=-2, imovel_id="IM039")  # visita passada sem resultado -> atenção
    assert any(i.tipo == "📝 Visita sem resultado registrado" for i in painel.calcular().atencao)
    assert painel.calcular(7).leads_novos == 2
    del b
