"""Tela "Conversar com o Agente": o chat do CLIENTE com o Sr. Agim (texto,
fotos e microfone), com o perfil identificado na barra lateral."""
from __future__ import annotations

import logging

import streamlit as st

from src.config import settings
from src.domain.cpf import formatar_cpf
from src.domain.entities import RemetenteMensagem
from src.domain.investimento import formatar_moeda
from src.interface.paginas.comum import _mostrar_mensagem, _texto_seguro


def pagina_chat(container) -> None:
    st.title(f"🏠 {settings.agente_nome} — Agente Imobiliário")
    st.caption(
        f"Você está falando com {settings.agente_nome}. Converse como se fosse um "
        "cliente interessado em comprar, alugar ou investir. "
        + ("Modo demonstração (sem LLM real)." if not settings.usando_llm_real else "Powered by Azure OpenAI.")
    )

    if "lead_id" not in st.session_state:
        lead = container.conversation_service.obter_ou_criar_lead(None, canal="web")
        st.session_state.lead_id = lead.id

    lead = container.conversation_service.obter_ou_criar_lead(st.session_state.lead_id)

    with st.sidebar.expander("👤 Perfil identificado", expanded=True):
        if lead.cliente_identificado:
            st.write(f"**Nome:** {lead.nome}")
            st.write(f"**CPF:** {formatar_cpf(lead.cpf)}")
        else:
            st.caption("Cadastro ainda não concluído (nome + CPF).")
        perfil = lead.perfil
        st.write(f"**Intenção:** {perfil.intencao.value}")
        st.write(f"**Temperatura:** {perfil.temperatura.value}")
        st.write(f"**Região:** {perfil.regiao_interesse or '—'}")
        st.write(f"**Quartos:** {perfil.quartos_desejados or '—'}")
        st.write(f"**Faixa de preço:** {_faixa_de_preco(perfil.faixa_preco_min, perfil.faixa_preco_max)}")
        if perfil.urgencia:
            st.write(f"**Urgência:** {perfil.urgencia}")
        if perfil.ticket_investimento:
            st.write(f"**Valor para investir:** {formatar_moeda(perfil.ticket_investimento)}")
        if lead.agendamentos:
            st.write(f"**Agendamento:** {lead.agendamentos[-1].quando_sugerido}")

    if lead.historico and not lead.atendimento_encerrado and st.sidebar.button("✅ Encerrar atendimento"):
        container.conversation_service.processar_mensagem(lead, "encerrar atendimento")
        st.rerun()
    if st.sidebar.button("🔄 Começar nova conversa"):
        novo_lead = container.conversation_service.obter_ou_criar_lead(None, canal="web")
        st.session_state.lead_id = novo_lead.id
        st.rerun()

    for mensagem in lead.historico:
        papel = "user" if mensagem.remetente == RemetenteMensagem.LEAD else "assistant"
        with st.chat_message(papel):
            _mostrar_mensagem(container, mensagem.conteudo)

    if not lead.historico:
        # Este texto precisa ficar sincronizado com a primeira pergunta real
        # do IdentificacaoAgent (src/agents/identification_agent.py) — o
        # cadastro/identificação roda ANTES de perguntar intenção de compra/
        # aluguel/investimento, então a saudação não pode "furar a fila".
        with st.chat_message("assistant"):
            st.write(
                f"Olá! Sou o {settings.agente_nome} 👋 Antes de começarmos, me conta: "
                "você já falou com a gente antes? Se sim, me diga seu CPF que eu "
                "recupero nossa conversa. Se não, me diga seu nome completo e CPF "
                "para eu fazer seu cadastro."
            )

    if lead.atendimento_encerrado:
        ultima_do_agente = next(
            (m.conteudo for m in reversed(lead.historico) if m.remetente == RemetenteMensagem.AGENTE), "")
        if "nota" in ultima_do_agente.lower() and "1 a 5" in ultima_do_agente:
            st.info("✅ Atendimento encerrado. Se quiser, dê uma nota de 1 a 5 — ou mande qualquer mensagem para "
                    "continuar de onde parou.")
        else:
            st.info("✅ Atendimento encerrado. Quando quiser, é só mandar uma mensagem para continuar de onde parou.")

    # Resposta falada da última mensagem por voz (toca uma vez).
    audio_resposta = st.session_state.pop("audio_resposta", None)
    if audio_resposta:
        st.audio(audio_resposta, format="audio/mp3", autoplay=True)

    # Voz (Azure Speech): o cliente fala pelo microfone; o Sr. Agim transcreve
    # e responde em texto + áudio. Só aparece com AZURE_SPEECH_KEY no .env.
    audio = None
    if container.voz:
        audio = st.audio_input("🎤 Prefere falar? Grave sua mensagem", key=f"mic_{st.session_state.get('mic_n', 0)}")

    entrada_usuario = st.chat_input("Digite sua mensagem...")
    falou = False
    if audio is not None and not entrada_usuario:
        from src.infrastructure.voice.azure_speech_service import explicar_erro_de_voz

        erro_voz = None
        with st.spinner("Ouvindo..."):
            try:
                entrada_usuario = container.voz.fala_para_texto(audio.getvalue(), formato="wav")
            except Exception as erro:  # noqa: BLE001
                logging.getLogger("agente_sdr").exception("Falha ao transcrever o áudio do microfone")
                erro_voz, entrada_usuario = erro, ""
        st.session_state.mic_n = st.session_state.get("mic_n", 0) + 1  # limpa o gravador
        if erro_voz is not None:
            st.error(f"🎤 Não consegui transcrever: {explicar_erro_de_voz(erro_voz)}")
            return
        if not entrada_usuario:
            st.warning("🎤 Não entendi o que foi falado (áudio muito curto ou sem voz). Pode repetir ou escrever?")
            return
        falou = True
    if entrada_usuario:
        with st.chat_message("user"):
            st.markdown(_texto_seguro(("🎤 " if falou else "") + entrada_usuario))
        with st.chat_message("assistant"):
            with st.spinner("Digitando..."):
                lead_atualizado = container.conversation_service.processar_mensagem(
                    lead, entrada_usuario
                )
            resposta = lead_atualizado.historico[-1].conteudo
            _mostrar_mensagem(container, resposta)
        if falou and settings.voz_responder_em_audio:
            try:
                from src.infrastructure.voice.azure_speech_service import FORMATO_NAVEGADOR

                st.session_state.audio_resposta = container.voz.texto_para_fala(resposta, formato=FORMATO_NAVEGADOR)
            except Exception:  # noqa: BLE001 — sem áudio, a resposta escrita continua lá
                pass
        # Se o cliente foi reconhecido como recorrente (pelo CPF), a
        # conversa "trocou" para o lead já existente dele — atualizamos a
        # sessão para apontar para esse lead (com todo o histórico antigo).
        if lead_atualizado.id != st.session_state.lead_id:
            st.session_state.lead_id = lead_atualizado.id
        st.rerun()


def _faixa_de_preco(minimo, maximo) -> str:
    """'até R$ 600.000,00', 'a partir de R$ 2.000,00' ou 'R$ x a R$ y'."""
    if minimo and maximo:
        return f"{formatar_moeda(minimo)} a {formatar_moeda(maximo)}"
    if maximo:
        return f"até {formatar_moeda(maximo)}"
    if minimo:
        return f"a partir de {formatar_moeda(minimo)}"
    return "—"
