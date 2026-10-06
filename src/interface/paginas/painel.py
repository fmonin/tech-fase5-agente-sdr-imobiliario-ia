"""Tela "Painel da Imobiliária": visão do GESTOR (KPIs, funil, atenção agora,
demanda × oferta, corretores, agente de IA, leads)."""
from __future__ import annotations


import pandas as pd
import plotly.express as px
import streamlit as st

from src.domain.investimento import formatar_moeda
from src.services.painel_gestor_service import ETAPAS as ETAPAS_FUNIL
from src.interface.paginas.comum import _secao_resumo_do_lead, _texto_seguro


def pagina_dashboard(container) -> None:
    """Painel da Imobiliária — visão do GESTOR sobre a operação inteira (a
    "Área do Corretor" é o dia a dia de cada corretor)."""
    st.title("📊 Painel da Imobiliária")
    st.caption("Visão do gestor: como está a operação, onde perdemos negócio e o que precisa de atenção agora.")

    periodos = {"Últimos 7 dias": 7, "Últimos 30 dias": 30, "Tudo": None}
    rotulo = st.radio("Período", list(periodos), index=2, horizontal=True)
    p = container.painel_gestor.calcular(periodos[rotulo])

    def pct(v):
        return "—" if v is None else f"{v * 100:.0f}%"

    # 1. Indicadores
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Leads novos", p.leads_novos)
    c2.metric("Qualificados pela IA", pct(p.taxa_qualificacao), help="Leads com dados suficientes / interesse real")
    c3.metric("Qualificado → visita", pct(p.taxa_agendamento))
    c4.metric("Visita → negócio", pct(p.taxa_fechamento))
    c5, c6, c7, c8 = st.columns(4)
    c8.metric("Captações", p.captacoes, help="Imóveis de clientes para venda/locação com avaliação agendada")
    c5.metric("Valor fechado", _moeda_curta(p.valor_fechado), help=formatar_moeda(p.valor_fechado))
    t1 = p.tempo_primeira_resposta_s
    c6.metric("1ª resposta (mediana)", "—" if t1 is None else ("< 1 s" if t1 < 1 else f"{t1:.0f} s"),
              help="Tempo até o Sr. Agim responder o lead (24h por dia, sem fila)")
    c7.metric("Até agendar (mediana)", "—" if p.tempo_ate_agendamento_h is None
              else (f"{p.tempo_ate_agendamento_h * 60:.0f} min" if p.tempo_ate_agendamento_h < 1
                    else f"{p.tempo_ate_agendamento_h:.1f} h"), help="Do primeiro contato até a visita marcada")

    aba_funil, aba_atencao, aba_demanda, aba_equipe, aba_ia, aba_leads = st.tabs(
        ["🔻 Funil", "🚨 Atenção agora", "🗺️ Demanda × oferta", "👥 Corretores", "🤖 Agente de IA", "📋 Leads"]
    )

    # 2. Funil
    with aba_funil:
        etapas, valores = zip(*p.funil)
        st.plotly_chart(px.funnel(x=list(valores), y=list(etapas), title="Funil da operação (todos os corretores)"),
                        use_container_width=True)
        perdas = [(etapas[i], valores[i] - valores[i + 1]) for i in range(len(valores) - 1) if valores[i]]
        if perdas:
            maior = max(perdas, key=lambda x: x[1])
            if maior[1] > 0:
                st.info(f"Maior perda: depois de **{maior[0]}** ({maior[1]} ficaram pelo caminho).")

    # 5. Atenção agora
    with aba_atencao:
        if not p.atencao:
            st.success("Nada pendente agora. 🎉")
        else:
            st.caption(f"{len(p.atencao)} item(ns) — os mais urgentes primeiro.")
            st.dataframe(pd.DataFrame([
                {"Situação": i.tipo, "Cliente": i.cliente, "Detalhe": i.detalhe, "Corretor": i.corretor}
                for i in p.atencao
            ]), use_container_width=True, hide_index=True)

    # 4. Demanda × oferta
    with aba_demanda:
        if not p.demanda:
            st.caption("Ainda não há leads com região e intenção definidas.")
        else:
            df = pd.DataFrame([{
                "Região": d.regiao, "Intenção": d.intencao, "Clientes": d.clientes,
                "Sem nenhuma opção": d.clientes_sem_opcao, "Imóveis compatíveis": d.imoveis_compativeis,
                "Orçamento médio": formatar_moeda(d.faixa_media) if d.faixa_media else "—",
            } for d in p.demanda])
            st.plotly_chart(px.bar(df, x="Região", y="Clientes", color="Intenção", barmode="stack",
                                   title="O que os clientes procuram"), use_container_width=True)
            faltas = [d for d in p.demanda if d.clientes_sem_opcao]
            if faltas:
                st.warning("**Onde falta imóvel (oportunidade de captação):**  \n" + "  \n".join(
                    _texto_seguro(f"- {d.regiao} ({d.intencao}): {d.clientes_sem_opcao} de {d.clientes} cliente(s) "
                                  f"sem nenhuma opção compatível"
                                  + (f" — orçamento médio {formatar_moeda(d.faixa_media)}" if d.faixa_media else ""))
                    for d in faltas))
            st.dataframe(df, use_container_width=True, hide_index=True)
        st.markdown("**Por que os negócios não fecham (todos os corretores)**")
        if p.motivos_perda:
            st.plotly_chart(px.bar(x=list(p.motivos_perda.values()), y=list(p.motivos_perda), orientation="h",
                                   labels={"x": "ocorrências", "y": ""}), use_container_width=True)
        else:
            st.caption("Sem perdas registradas.")

    # 3. Corretores
    with aba_equipe:
        st.caption("Visão acumulada por corretor. \"Sem resultado\" = visitas que já aconteceram e não foram registradas.")
        st.dataframe(pd.DataFrame([{
            "Corretor": l.corretor.nome, "Área": ", ".join(l.corretor.zonas_atuacao), "Carteira": l.carteira,
            "Sem visita": l.sem_visita, "Visitas": l.visitas, "Realizadas": l.realizadas, "Fechados": l.fechados,
            "Fechamento": pct(l.taxa_fechamento), "Valor fechado": formatar_moeda(l.valor_fechado),
            "Cancelamentos": l.cancelamentos, "Sem resultado": l.sem_resultado,
        } for l in p.corretores]), use_container_width=True, hide_index=True)
        carga = pd.DataFrame([{"Corretor": l.corretor.nome, "Clientes em carteira": l.carteira} for l in p.corretores])
        st.plotly_chart(px.bar(carga, x="Corretor", y="Clientes em carteira", title="Carga de clientes por corretor"),
                        use_container_width=True)

    # 6. Agente de IA
    with aba_ia:
        ia = p.ia
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("Conversas", ia["conversas"], help=f"Clientes identificados: {ia['identificados']}")
        i2.metric("Mensagens do cliente / conversa",
                  "—" if ia["msgs_por_conversa"] is None else f"{ia['msgs_por_conversa']:.1f}".replace(".", ","))
        i3.metric("Mensagens até agendar", "—" if ia["msgs_ate_agendar"] is None else f"{ia['msgs_ate_agendar']:.1f}".replace(".", ","))
        i4.metric("Reengajamento do follow-up", pct(ia["taxa_reengajamento"]),
                  help=f"{ia['followups']} follow-up(s) enviados; % que voltou a responder")
        notas = ia.get("notas") or []
        n1, n2 = st.columns(2)
        n1.metric("Satisfação (nota 1–5)", "—" if not notas else f"{sum(notas) / len(notas):.1f}".replace(".", ","),
                  help=f"{len(notas)} avaliação(ões) dadas pelos clientes ao encerrar o atendimento")
        n2.metric("Atendimentos encerrados pelo cliente", ia.get("encerrados", 0))
        col_a, col_b = st.columns(2)
        if ia["canais"]:
            col_a.plotly_chart(px.pie(names=list(ia["canais"]), values=list(ia["canais"].values()),
                                      title="Conversas por canal"), use_container_width=True)
        if ia["temas"]:
            col_b.plotly_chart(px.bar(x=list(ia["temas"].values()), y=list(ia["temas"]), orientation="h",
                                      title="Assuntos mais perguntados", labels={"x": "mensagens", "y": ""}),
                               use_container_width=True)

    # 7. Leads
    with aba_leads:
        if not p.leads:
            st.info("Ainda não há leads no período. Converse com o agente para gerar dados aqui.")
        else:
            df = pd.DataFrame(p.leads)
            f1, f2, f3 = st.columns(3)
            temp = f1.multiselect("Temperatura", sorted(df["Temperatura"].unique()))
            inten = f2.multiselect("Intenção", sorted(df["Intenção"].unique()))
            corr = f3.multiselect("Corretor", sorted(df["Corretor"].unique()))
            if temp:
                df = df[df["Temperatura"].isin(temp)]
            if inten:
                df = df[df["Intenção"].isin(inten)]
            if corr:
                df = df[df["Corretor"].isin(corr)]
            st.plotly_chart(px.bar(df.groupby("Etapa", as_index=False).size().rename(columns={"size": "Leads"}),
                                   x="Etapa", y="Leads", title="Leads por etapa do funil",
                                   category_orders={"Etapa": ETAPAS_FUNIL}), use_container_width=True)
            df["Última interação"] = pd.to_datetime(df["Última interação"]).dt.strftime("%d/%m %H:%M")
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.subheader("Resumos para os corretores")
            leads_por_nome = {(l.nome or f"Visitante {l.id[:6]}"): l for l in container.dashboard_service.listar_leads()}
            escolhido = st.selectbox("Cliente", list(df["Cliente"]))
            if escolhido and escolhido in leads_por_nome:
                _secao_resumo_do_lead(container, leads_por_nome[escolhido], chave=f"painel_{leads_por_nome[escolhido].id}")

    with st.expander("🛠️ Depuração — eventos de observabilidade (últimos 50)", expanded=False):
        eventos = container.dashboard_service.listar_eventos_recentes(50)
        if eventos:
            st.dataframe(pd.DataFrame(eventos), use_container_width=True, hide_index=True)
        else:
            st.write("Nenhum evento registrado ainda.")


def _moeda_curta(valor: float) -> str:
    """Valor compacto para caber no cartão: R$ 10,4 mil · R$ 1,25 mi."""
    if valor >= 1_000_000:
        return "R$ " + f"{valor / 1_000_000:.2f}".replace(".", ",") + " mi"
    if valor >= 1_000:
        return "R$ " + f"{valor / 1_000:.1f}".replace(".", ",") + " mil"
    return formatar_moeda(valor)
