"""Tela "Área do Corretor": login simplificado, avisos, abas (agenda,
pós-visita, clientes, imóveis da área, imóveis novos, captações, desempenho)
e o assistente com menu "O que deseja fazer?"."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from src.agents.menu_corretor_agent import MENU_ABERTO, texto_menu
from src.config import settings
from src.domain.entities import Imovel
from src.domain.feedback_visita import MOTIVOS_PERDA, RESULTADOS_VISITA
from src.domain.investimento import formatar_moeda
from src.interface.paginas.comum import _quando, _secao_resumo_do_lead, _texto_seguro


def pagina_area_corretor(container) -> None:
    st.title("🧑‍💼 Área do Corretor")
    st.caption(
        "Consulte, cancele ou remarque compromissos em linguagem natural — o LLM "
        "usa SOMENTE os agendamentos reais atribuídos a você e nada muda sem a sua "
        "confirmação."
    )

    corretores = container.corretor_repository.listar_todos()
    if not corretores:
        st.info("Nenhum corretor cadastrado ainda.")
        return

    opcoes = {f"{c.nome} — {', '.join(c.zonas_atuacao)}": c for c in corretores}
    escolha = st.selectbox(
        "Selecione seu nome (login simplificado da POC, sem senha):",
        list(opcoes.keys()),
    )
    corretor = opcoes[escolha]

    if "corretor_selecionado_id" not in st.session_state or st.session_state.corretor_selecionado_id != corretor.id:
        st.session_state.corretor_selecionado_id = corretor.id
        st.session_state.historico_agenda = []
        st.session_state.acao_pendente_agenda = None
        st.session_state.avisos_corretor = []
        st.session_state.menu_pendente = dict(MENU_ABERTO)

    with st.sidebar.expander("🧑‍💼 Corretor logado", expanded=True):
        st.write(f"**Nome:** {corretor.nome}")
        st.write(f"**Área de atuação:** {', '.join(corretor.zonas_atuacao)}")
        if corretor.email:
            st.write(f"**E-mail:** {corretor.email}")

    # Ao "logar" (selecionar o corretor), o LLM já abre a conversa
    # apresentando a agenda de forma simpática. Gerado uma vez por login
    # (fica no session_state) para não chamar o LLM a cada rerun da tela.
    if not st.session_state.get("historico_agenda"):
        with st.spinner("Preparando sua agenda..."):
            saudacao = container.agenda_query_agent.saudar(corretor)
        # Logo depois da agenda, o menu com tudo que o corretor pode fazer.
        st.session_state.historico_agenda = [("assistant", f"{saudacao}\n\n{texto_menu(corretor.nome)}")]
        st.session_state.menu_pendente = dict(MENU_ABERTO)

    # Avisos de cancelamento/remarcação feitos pelo CLIENTE: aparecem uma vez
    # no chat (e ficam num destaque durante a sessão), checados a cada tela.
    avisos = container.agenda_service.avisos_do_cliente_para_corretor(corretor.id)
    if avisos:
        st.session_state.setdefault("avisos_corretor", []).extend(avisos)
        st.session_state.historico_agenda.append(
            ("assistant", "🔔 **Aviso de agenda:**\n" + "\n".join(f"- {a}" for a in avisos))
        )
        st.toast("🔔 Um cliente alterou a sua agenda", icon="🔔")
    if st.session_state.get("avisos_corretor"):
        st.warning("🔔 **Avisos de clientes:**  \n" + "  \n".join(
            _texto_seguro(a) for a in st.session_state.avisos_corretor))

    if st.session_state.get("aviso_cancelamento"):
        tipo_aviso, texto_aviso = st.session_state.pop("aviso_cancelamento")
        (st.success if tipo_aviso == "ok" else st.error)(texto_aviso)

    aba_agenda, aba_pos, aba_clientes, aba_area, aba_novos, aba_captacao, aba_desempenho = st.tabs(
        ["📅 Agenda", "📝 Pós-visita", "🔎 Clientes", "🏘️ Imóveis da área", "🏠 Imóveis novos", "🏷️ Captações",
         "📊 Desempenho"]
    )
    with aba_agenda:
        _secao_meus_agendamentos(container, corretor)
    with aba_pos:
        _secao_pos_visita(container, corretor)
    with aba_clientes:
        _secao_clientes_sem_visita(container, corretor)
        _secao_sugestoes_para_agendados(container, corretor)
    with aba_area:
        _secao_imoveis_da_area(container, corretor)
    with aba_novos:
        _secao_imoveis_novos(container, corretor)
    with aba_captacao:
        _secao_captacoes(container, corretor)
    with aba_desempenho:
        _secao_desempenho(container, corretor)

    st.subheader(f"💬 Assistente do corretor ({settings.agente_nome})")
    for papel, conteudo in st.session_state.get("historico_agenda", []):
        with st.chat_message(papel):
            st.markdown(_texto_seguro(conteudo).replace("\n", "  \n"))

    pergunta = st.chat_input('Escolha uma opção do menu (ex.: "2") ou escreva. Ex.: "muda a visita do Fernando para sexta às 14h"')
    if pergunta:
        st.session_state.historico_agenda.append(("user", pergunta))
        with st.chat_message("user"):
            st.markdown(_texto_seguro(pergunta))
        with st.chat_message("assistant"):
            with st.spinner("Verificando sua agenda..."):
                # Cancelar/remarcar pelo chat: o LLM interpreta, o código valida
                # e NADA muda sem o corretor confirmar ("sim"/"não"). A ação
                # aguardando confirmação fica guardada entre um turno e outro.
                # Menu: se há cancelamento/remarcação aguardando confirmação,
                # ela tem prioridade; senão o menu tenta (número ou palavra).
                resposta_menu = None
                if not st.session_state.get("acao_pendente_agenda"):
                    resposta_menu = container.menu_corretor.processar(
                        corretor, pergunta, st.session_state.get("menu_pendente")
                    )
                if resposta_menu is not None:
                    texto_resposta = resposta_menu.texto
                    st.session_state.menu_pendente = resposta_menu.pendente
                else:
                    resultado = container.gestao_agenda_agent.processar(
                        corretor, pergunta, st.session_state.get("acao_pendente_agenda")
                    )
                    texto_resposta = resultado.texto
                    st.session_state.acao_pendente_agenda = resultado.acao_pendente
                    st.session_state.menu_pendente = dict(MENU_ABERTO)
            st.markdown(_texto_seguro(texto_resposta).replace("\n", "  \n"))
        st.session_state.historico_agenda.append(("assistant", texto_resposta))
        st.rerun()


def _secao_clientes_sem_visita(container, corretor) -> None:
    """Clientes que procuraram imóveis na área do corretor e não agendaram."""
    clientes = container.carteira_corretor.clientes_sem_visita(corretor)
    area = ", ".join(corretor.zonas_atuacao)
    with st.expander(f"🔎 Clientes da sua área sem visita agendada ({len(clientes)})"):
        st.caption(
            f"Leads que procuraram imóveis em **{area}** e ainda não marcaram visita/reunião. "
            "Os mais quentes e recentes aparecem primeiro — boa lista para ligar hoje."
        )
        if not clientes:
            st.caption("Nenhum cliente pendente na sua área no momento. 🎉")
        for i, cliente in enumerate(clientes):
            lead = cliente.lead
            temperatura = {"quente": "🔥 quente", "morno": "🌤️ morno", "frio": "❄️ frio"}.get(
                lead.perfil.temperatura.value, lead.perfil.temperatura.value
            )
            ultima = lead.ultima_interacao_em.strftime("%d/%m/%Y %Hh%M")
            linhas = [
                f"**{lead.nome or 'Cliente'}** · {temperatura} · canal {lead.canal} · última conversa {ultima} (UTC)",
                f"Procura: {cliente.interesse or 'interesse ainda não definido'}",
                f"Situação: _{cliente.situacao}_",
            ]
            if cliente.imovel_interesse:
                linhas.append(f"Imóvel de interesse: {cliente.imovel_interesse.titulo} ({cliente.imovel_interesse.bairro})")
            st.markdown(_texto_seguro("  \n".join(linhas)))
            with st.popover("📝 Resumo do cliente"):
                _secao_resumo_do_lead(container, lead, chave=f"semvisita_{lead.id}")
            if i < len(clientes) - 1:
                st.divider()


def _secao_sugestoes_para_agendados(container, corretor) -> None:
    """Para cada cliente agendado: o imóvel escolhido + outros que o corretor
    pode oferecer na visita (aumenta a chance de fechar negócio)."""
    itens = container.carteira_corretor.sugestoes_para_agendados(corretor)
    with st.expander(f"💡 Clientes agendados — imóveis para sugerir ({len(itens)})"):
        st.caption(
            "Além do imóvel escolhido pelo cliente, opções parecidas (mesmo bairro → vizinhos → mesma "
            "região, preço e quartos próximos; para investidor, maior retorno) para levar na visita. "
            "🆕 = o cliente ainda não viu no chat."
        )
        if not itens:
            st.caption("Nenhum cliente agendado no momento.")
        for i, item in enumerate(itens):
            ag = item.agendamento
            tipo = ag.tipo_texto.capitalize()
            st.markdown(_texto_seguro(
                f"**{ag.cliente_nome or 'Cliente'}** · {tipo} em {_quando(ag)} · {item.intencao} · _status: {ag.status}_"
            ))
            if item.imovel_escolhido:
                st.markdown(_texto_seguro(
                    f"⭐ **Escolhido pelo cliente:** {item.imovel_escolhido.id} — {item.imovel_escolhido.titulo}  \n"
                    + item.resumo_escolhido.replace("\n", "  \n")
                ))
            else:
                st.caption("O cliente ainda não escolheu um imóvel específico.")
            if item.sugestoes:
                st.markdown("**Sugestões para oferecer:**")
                for n, sug in enumerate(item.sugestoes, start=1):
                    novo = "🆕 " if not sug.ja_mostrado_ao_cliente else ""
                    st.markdown(_texto_seguro(
                        f"{n}) {novo}**{sug.imovel.id} — {sug.imovel.titulo}**  \n"
                        + sug.resumo.replace("\n", "  \n")
                        + f"  \n_Por quê: {sug.motivo}_"
                    ))
            else:
                st.caption("Não encontrei imóveis parecidos na base para sugerir.")
            if i < len(itens) - 1:
                st.divider()


def _secao_pos_visita(container, corretor) -> None:
    """Registrar o resultado das visitas (funil) e ver o retorno dos clientes."""
    st.markdown("#### 📝 Resultado das visitas")
    st.caption(
        "Registre como foi cada visita: alimenta o seu funil de vendas e, quando o cliente não gosta, o motivo "
        f"volta para o {settings.agente_nome}, que passa a sugerir opções melhores (ex.: mais baratas)."
    )
    agora = datetime.now()
    visitas = container.pos_visita.visitas_para_registrar(corretor.id)
    if not visitas:
        st.caption("Nenhuma visita confirmada aguardando resultado. 👍")
    titulo_negociacao = False
    for a in visitas:
        if a.resultado_visita and not titulo_negociacao:
            titulo_negociacao = True
            st.markdown("#### 🤝 Negociações em andamento")
            st.caption("Quando a proposta for aceita, marque **🤝 Negócio fechado** e informe o valor — "
                       "entra na hora no seu desempenho.")
        ja_foi = a.data_hora is not None and a.data_hora <= agora
        tipo = a.tipo_texto.capitalize()
        imovel = f" — {a.imovel_titulo}" if a.imovel_titulo else ""
        with st.form(key=f"form_resultado_{a.id}"):
            st.markdown(_texto_seguro(
                f"**{a.cliente_nome or 'Cliente'}** · {tipo} {_quando(a)}{imovel}"
                + ("" if ja_foi else "  \n_⏳ ainda vai acontecer_")
                + (f"  \nSituação atual: **{RESULTADOS_VISITA[a.resultado_visita]}**"
                   + (f" · {formatar_moeda(a.valor_negociado)}" if a.valor_negociado else "")
                   if a.resultado_visita else "")
            ))
            if a.feedback_cliente:
                st.markdown(_texto_seguro(f"💬 Cliente disse: “{a.feedback_cliente}”"))
            col1, col2 = st.columns(2)
            chaves = list(RESULTADOS_VISITA)
            proximo = {"gostou": "proposta", "proposta": "fechado"}.get(a.resultado_visita or "", "gostou")
            resultado = col1.selectbox("Resultado", chaves, index=chaves.index(proximo),
                                       format_func=RESULTADOS_VISITA.get, key=f"res_{a.id}")
            motivo = col2.selectbox("Motivo (se não gostou / não fechou)", MOTIVOS_PERDA, key=f"mot_{a.id}")
            col3, col4 = st.columns(2)
            valor = col3.number_input("Valor da proposta / do negócio (R$)", min_value=0.0, step=1000.0,
                                      value=float(a.valor_negociado or 0), key=f"val_{a.id}")
            observacao = col4.text_input("Observação (opcional)", key=f"obs_{a.id}")
            if st.form_submit_button("Salvar", type="primary"):
                ok, texto = container.pos_visita.registrar_resultado(
                    a.id, corretor.id, resultado, motivo if resultado == "nao_gostou" else None, observacao,
                    valor=valor or None)
                st.session_state.aviso_pos_visita = texto
                st.rerun()

    if st.session_state.get("aviso_pos_visita"):
        st.success(st.session_state.pop("aviso_pos_visita"))
    registrados = [a for a in container.agenda_repository.listar_por_corretor(corretor.id)
                   if a.resultado_visita in RESULTADOS_VISITA and a.resultado_visita not in ("gostou", "proposta")]
    if registrados:
        st.markdown("#### ✅ Já registrados")
        for a in sorted(registrados, key=lambda x: x.resultado_em or datetime.min, reverse=True)[:10]:
            motivo = f" ({a.motivo_resultado})" if a.motivo_resultado else ""
            obs = f" — _{a.observacao_resultado}_" if a.observacao_resultado else ""
            valor = f" — {formatar_moeda(a.valor_negociado)}" if a.valor_negociado else ""
            st.markdown(_texto_seguro(
                f"- {a.cliente_nome or 'Cliente'} · {_quando(a)}: {RESULTADOS_VISITA[a.resultado_visita]}{valor}{motivo}{obs}"))


def _secao_imoveis_novos(container, corretor) -> None:
    """Cadastro de imóvel + match automático com os clientes que procuram algo assim."""
    st.markdown("#### 🏠 Cadastrar imóvel novo")
    st.caption("Ao salvar, mostro na hora os clientes que procuram exatamente isso — e você avisa com um clique.")
    with st.form(key="form_novo_imovel", clear_on_submit=True):
        titulo = st.text_input("Título", placeholder="Ex.: Apartamento reformado perto do metrô")
        c1, c2, c3 = st.columns(3)
        negocio = c1.selectbox("Negócio", ["venda", "aluguel"])
        tipo = c2.selectbox("Tipo", ["Apartamento", "Casa", "Studio", "Kitnet", "Cobertura", "Sala comercial"])
        bairro = c3.selectbox("Bairro", container.match_imoveis.bairros_disponiveis())
        c4, c5, c6, c7 = st.columns(4)
        preco = c4.number_input("Preço (R$ — venda total ou aluguel/mês)", min_value=0.0, step=1000.0)
        quartos = c5.number_input("Quartos", min_value=0, max_value=10, value=2)
        suites = c6.number_input("Suítes", min_value=0, max_value=10, value=0)
        vagas = c7.number_input("Vagas", min_value=0, max_value=10, value=1)
        c8, c9, c10 = st.columns(3)
        metragem = c8.number_input("Metragem (m²)", min_value=0.0, value=60.0, step=1.0)
        condominio = c9.number_input("Condomínio (R$/mês, 0 = sem)", min_value=0.0, step=50.0)
        investimento = c10.checkbox("Bom para investimento")
        descricao = st.text_area("Descrição", height=70)
        if st.form_submit_button("Cadastrar e buscar clientes", type="primary"):
            if not titulo or preco <= 0:
                st.error("Preencha pelo menos o título e o preço.")
            else:
                item = container.match_imoveis.cadastrar(Imovel(
                    id="", titulo=titulo, tipo_negocio=negocio, finalidade_investimento=investimento, zona="",
                    bairro=bairro, preco=preco, quartos=int(quartos), metragem=metragem,
                    descricao=descricao or titulo, tipo_imovel=tipo, suites=int(suites), vagas=int(vagas),
                    condominio=condominio or None,
                ), corretor)
                st.session_state.aviso_match = (
                    f"Imóvel {item.imovel.id} cadastrado! {len(item.clientes)} cliente(s) compatível(is).")
                st.rerun()
    if st.session_state.get("aviso_match"):
        st.success(st.session_state.pop("aviso_match"))

    st.markdown("#### 🎯 Imóveis novos × clientes interessados (últimos 30 dias)")
    itens = container.match_imoveis.imoveis_novos_com_match(corretor)
    if not itens:
        st.caption("Nenhum imóvel novo na sua área ainda.")
    for item in itens:
        im = item.imovel
        st.markdown(_texto_seguro(
            f"**{im.id} — {im.titulo}** · {im.bairro} ({im.zona}) · {im.tipo_negocio} · {formatar_moeda(im.preco)}"
            f" · {im.quartos} quarto(s)"))
        if not item.clientes:
            st.caption("Nenhum cliente compatível por enquanto.")
        for c in item.clientes:
            col_info, col_btn = st.columns([4, 1])
            col_info.markdown(_texto_seguro(f"- **{c.lead.nome}** — {c.motivo}"))
            if c.ja_avisado:
                col_btn.caption("✔️ avisado")
            elif col_btn.button("📣 Avisar", key=f"avisar_{im.id}_{c.lead.id}"):
                container.match_imoveis.avisar_cliente(c.lead.id, im.id, corretor)
                st.session_state.aviso_match = f"{c.lead.nome} foi avisado(a) pelo {settings.agente_nome}."
                st.rerun()
        st.divider()


def _secao_imoveis_da_area(container, corretor) -> None:
    """Todos os imóveis da base na área de atuação do corretor, com filtros."""
    imoveis = container.carteira_corretor.imoveis_da_area(corretor)
    area = ", ".join(corretor.zonas_atuacao)
    st.markdown(f"#### 🏘️ Imóveis da sua área ({area})")
    if not imoveis:
        st.caption("Nenhum imóvel cadastrado na sua área.")
        return
    f1, f2, f3, f4 = st.columns(4)
    negocio = f1.selectbox("Negócio", ["Todos", "venda", "aluguel"], key="area_negocio")
    bairros = f2.multiselect("Bairro", sorted({im.bairro for im in imoveis}), key="area_bairros")
    quartos_min = f3.number_input("Quartos (mín.)", min_value=0, max_value=10, value=0, key="area_quartos")
    teto = f4.number_input("Preço até (R$, 0 = sem limite)", min_value=0.0, step=10000.0, key="area_teto")
    filtrados = [
        im for im in imoveis
        if (negocio == "Todos" or im.tipo_negocio == negocio) and (not bairros or im.bairro in bairros)
        and im.quartos >= quartos_min and (not teto or im.preco <= teto)
    ]
    venda = sum(1 for im in filtrados if im.tipo_negocio == "venda")
    m1, m2, m3 = st.columns(3)
    m1.metric("Imóveis", len(filtrados))
    m2.metric("À venda", venda)
    m3.metric("Para alugar", len(filtrados) - venda)
    if not filtrados:
        st.caption("Nenhum imóvel com esses filtros.")
        return
    interessados = {im.id: len(container.match_imoveis.clientes_para_imovel(im)) for im in filtrados}
    st.dataframe(pd.DataFrame([{
        "ID": im.id, "Título": im.titulo, "Tipo": im.tipo_imovel, "Negócio": im.tipo_negocio, "Bairro": im.bairro,
        "Quartos": im.quartos, "Suítes": im.suites, "Vagas": im.vagas, "m²": round(im.metragem),
        "Preço": formatar_moeda(im.preco) + ("/mês" if im.tipo_negocio == "aluguel" else ""),
        "Condomínio": formatar_moeda(im.condominio) if im.condominio else "—",
        "Clientes interessados": interessados[im.id],
        "Origem": "captação/cadastro" if im.cadastrado_em else "base",
    } for im in filtrados]), use_container_width=True, hide_index=True)
    st.caption("\"Clientes interessados\" = clientes cuja busca combina com o imóvel (mesmo critério do match). "
               "Para avisá-los de um imóvel novo, use a aba 🏠 Imóveis novos.")


def _secao_captacoes(container, corretor) -> None:
    """Imóveis de clientes (venda/locação) com avaliação agendada pelo Sr. Agim."""
    from src.services.captacao_service import RESULTADOS_CAPTACAO

    st.markdown("#### 🏷️ Captação de imóveis")
    st.caption(f"Clientes que querem vender ou alugar o próprio imóvel: o {settings.agente_nome} fez o cadastro, "
               "estimou o valor e agendou a avaliação. Depois da visita, registre o resultado — captado entra na "
               "base na hora, com o match de clientes interessados.")
    if st.session_state.get("aviso_captacao"):
        st.success(st.session_state.pop("aviso_captacao"))
    itens = container.captacao.listar(corretor.id, incluir_finalizadas=True)
    if not itens:
        st.caption("Nenhuma captação por enquanto.")
    for item in itens:
        a = item.agendamento
        finalizada = a.resultado_visita in ("captado", "nao_captado")
        with st.form(key=f"form_captacao_{a.id}"):
            st.markdown(_texto_seguro(
                f"**{a.cliente_nome or 'Cliente'}** · avaliação {_quando(a)} · {item.finalidade}"
                + (f"  \nSituação: **{RESULTADOS_CAPTACAO[a.resultado_visita]}**" if a.resultado_visita else "")
                + (f" · {a.imovel_id}" if a.imovel_id else "")
            ))
            if a.detalhes:
                st.markdown(_texto_seguro(a.detalhes).replace("\n", "  \n"))
            if finalizada:
                st.form_submit_button("Finalizada", disabled=True)
                continue
            col1, col2, col3 = st.columns(3)
            chaves = list(RESULTADOS_CAPTACAO)
            resultado = col1.selectbox("Resultado", chaves, format_func=RESULTADOS_CAPTACAO.get, key=f"cap_res_{a.id}")
            valor = col2.number_input("Valor de anúncio (R$)", min_value=0.0, step=1000.0, key=f"cap_val_{a.id}")
            motivo = col3.text_input("Motivo (se não captado)", key=f"cap_mot_{a.id}")
            if st.form_submit_button("Salvar", type="primary"):
                ok, texto, _, _ = container.captacao.registrar_resultado(
                    a.id, corretor, resultado, valor or None, motivo)
                st.session_state.aviso_captacao = texto
                st.rerun()


def _secao_desempenho(container, corretor) -> None:
    d = container.desempenho_corretor.calcular(corretor)

    def pct(v):
        return "—" if v is None else f"{v * 100:.0f}%"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Visitas realizadas", d.visitas_realizadas)
    c2.metric("Visita → proposta", pct(d.taxa_visita_proposta))
    c3.metric("Negócios fechados", d.fechados,
              help=f"Taxa de fechamento: {pct(d.taxa_fechamento)} · Total: {formatar_moeda(d.valor_fechado)}")
    c4.metric("Comparecimento", pct(d.taxa_comparecimento), help=f"Não compareceram: {d.no_show}")
    if d.sem_resultado:
        st.warning(f"{d.sem_resultado} visita(s) já aconteceram e estão sem resultado — registre na aba 📝 Pós-visita.")

    etapas, valores = zip(*d.funil)
    fig = px.funnel(x=list(valores), y=list(etapas), title="Funil de vendas")
    st.plotly_chart(fig, use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Por que não fecharam**")
        if d.motivos_perda:
            st.plotly_chart(px.bar(x=list(d.motivos_perda.values()), y=list(d.motivos_perda), orientation="h",
                                   labels={"x": "visitas", "y": ""}), use_container_width=True)
        else:
            st.caption("Sem perdas registradas.")
    with col_b:
        st.markdown("**Agenda**")
        st.write(f"Cancelados pelo cliente: **{d.cancelados_cliente}** · por você: **{d.cancelados_corretor}**")
        st.write(f"Remarcações: **{d.remarcados}**")
        st.write(f"Valor fechado: **{_texto_seguro(formatar_moeda(d.valor_fechado))}** · "
                 f"Propostas em aberto: **{d.propostas_em_aberto}**")
        if d.feedbacks:
            st.write("Retorno dos clientes no pós-visita: " + ", ".join(f"{k}: **{v}**" for k, v in d.feedbacks.items()))


def _secao_meus_agendamentos(container, corretor) -> None:
    """Lista os agendamentos ativos do corretor, cada um com "Cancelar".

    O cancelamento passa pelo `AgendaService` (atualiza agenda, histórico do
    lead e CRM). O cliente é avisado pelo Sr. Agim na próxima vez que se
    identificar pelo CPF.
    """
    ativos = container.agenda_service.agendamentos_ativos(corretor.id)
    st.metric("Agendamentos ativos", len(ativos))

    cancelados_cliente = [
        a for a in container.agenda_repository.listar_por_corretor(corretor.id)
        if a.status == "cancelado" and a.cancelado_por == "cliente"
        and a.cancelado_em and a.cancelado_em >= datetime.utcnow() - timedelta(days=7)
    ]
    with st.expander(f"📅 Meus agendamentos ({len(ativos)})", expanded=True):
        if cancelados_cliente:
            st.warning(
                "Cancelados pelo cliente (últimos 7 dias): "
                + "; ".join(f"{a.cliente_nome or 'cliente'} — {_quando(a)}" for a in cancelados_cliente)
            )
        if not ativos:
            st.caption("Nenhum agendamento ativo no momento.")
        for agendamento in ativos:
            quando = (
                agendamento.data_hora.strftime("%d/%m/%Y às %Hh%M")
                if agendamento.data_hora
                else agendamento.quando_sugerido
            )
            tipo = agendamento.tipo_texto.capitalize()
            imovel = f" — {agendamento.imovel_titulo}" if agendamento.imovel_titulo else ""
            col_info, col_acao = st.columns([5, 1])
            with col_info:
                st.markdown(
                    _texto_seguro(
                        f"**{quando}** · {tipo} com **{agendamento.cliente_nome or 'cliente'}**"
                        f"{imovel}  \n_status: {agendamento.status}_"
                        + (f"  \n🔁 _{agendamento.motivo_alteracao or 'Remarcado'} — antes: {agendamento.horario_anterior}_"
                           if agendamento.horario_anterior else "")
                    )
                )
                if agendamento.detalhes:
                    with st.popover("🏷️ Ficha do imóvel (captação)"):
                        st.markdown(_texto_seguro(agendamento.detalhes).replace("\n", "  \n"))
                lead = container.conversation_service.buscar_lead(agendamento.lead_id)
                if lead:
                    # (popover: um expander não pode ficar dentro do expander da lista)
                    with st.popover("📝 Resumo do cliente"):
                        _secao_resumo_do_lead(container, lead, chave=f"agenda_{agendamento.id}")
            with col_acao:
                with st.popover("❌ Cancelar", use_container_width=True):
                    with st.form(key=f"form_cancelar_{agendamento.id}"):
                        motivo = st.text_input(
                            "Motivo (opcional — será informado ao cliente)",
                            placeholder="Ex.: imprevisto na agenda",
                        )
                        confirmar = st.form_submit_button("Confirmar cancelamento", type="primary")
                    if confirmar:
                        resultado = container.agenda_service.cancelar_pelo_corretor(
                            agendamento.id, corretor.id, motivo
                        )
                        if resultado.sucesso:
                            nome_cliente = agendamento.cliente_nome or "o cliente"
                            como = {
                                "telegram": f"{nome_cliente} já foi avisado(a) agora pelo Telegram",
                                "chat": f"o aviso ficou no chat de {nome_cliente}, que também é avisado(a) "
                                        "assim que se identificar",
                            }.get(resultado.cliente_avisado_por,
                                  f"{nome_cliente} será avisado(a) assim que se identificar")
                            st.session_state.aviso_cancelamento = ("ok", f"{tipo} de {quando} cancelada — {como}.")
                            st.session_state.historico_agenda.append(
                                ("assistant", f"Pronto! Cancelei a {tipo.lower()} de {quando} — {como}, "
                                              "e ofereci um novo horário. 😉")
                            )
                        else:
                            st.session_state.aviso_cancelamento = ("erro", resultado.mensagem)
                        st.rerun()
