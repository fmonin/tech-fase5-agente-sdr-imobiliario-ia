"""Entrypoint opcional: Sr. Agim (Agente SDR) via Telegram (diferencial do desafio).

Como usar:
    1. No Telegram, fale com o @BotFather, envie /newbot e copie o token.
    2. Preencha TELEGRAM_BOT_TOKEN no seu .env.
    3. Rode: python telegram_bot.py   (deixe o terminal aberto)
    4. Abra o seu bot no Telegram e envie /start.

Comandos do bot:  /start (começar)  ·  /novo (começar uma conversa do zero)
                  ·  /followup (dispara o follow-up agora — demonstração)
                  ·  /posvisita (pergunta "o que achou da visita?" agora — demonstração)
                  ·  /encerrar (o cliente encerra o atendimento)

Follow-up automático (Exemplo 3 do desafio): enquanto o bot roda, ele faz a
1ª verificação ~20 s após iniciar e depois a cada [followup] intervalo (config/settings.toml);
leads sem resposta há [followup] minutos_sem_resposta recebem uma mensagem de reengajamento
no chat. Cada verificação aparece no terminal ("[follow-up] ...").

Este arquivo é só a camada de "interface" para o Telegram — toda a lógica
de negócio (grafo multiagente, persistência, CRM) é REUTILIZADA do
`src.container`, exatamente como a interface Streamlit faz (Dependency
Inversion: as duas interfaces dependem do mesmo `ConversationService`).

Sessões: cada chat do Telegram aponta para um lead. Quando o cliente se
identifica pelo CPF e o sistema recupera o cadastro dele, o chat passa a
apontar para esse lead recuperado (`data/telegram_sessoes.json`) — sem isso,
o bot pediria o CPF de novo a cada mensagem.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.config import settings
from src.domain.midia import nome_ambiente, separar_midia
from src.infrastructure.notifications.telegram_sessoes import SessoesTelegram  # noqa: F401 (reexportado)

RAIZ = Path(__file__).resolve().parent
_log = logging.getLogger("telegram_bot")


def responder(container, sessoes: SessoesTelegram, chat_id: str, texto: str):
    """Processa a mensagem do chat e devolve (texto, mídias). Síncrono e sem
    dependência do Telegram — fácil de testar."""
    servico = container.conversation_service
    lead = servico.obter_ou_criar_lead(sessoes.lead_do_chat(chat_id), canal="telegram")
    lead_atualizado = servico.processar_mensagem(lead, texto)
    # Se o cliente foi reconhecido pelo CPF, o chat passa a usar o lead dele.
    sessoes.associar(chat_id, lead_atualizado.id)
    return separar_midia(lead_atualizado.historico[-1].conteudo)


def executar_followup(container, sessoes: SessoesTelegram, minutos: int) -> list[tuple[str, str]]:
    """Roda o FollowUpAgent e devolve [(chat_id, mensagem)] dos leads que têm
    chat no Telegram. Leads só da web recebem a mensagem no histórico (veem ao
    voltar para a página). Registra o evento `followup_disparado` (dashboard)."""
    envios: list[tuple[str, str]] = []
    for enviado in container.followup_agent.executar_para_leads_inativos(minutos):
        chats = sessoes.chats_do_lead(enviado.lead.id)
        envios.extend((chat_id, enviado.mensagem) for chat_id in chats)
        _registrar_followup(container, enviado.lead.id, bool(chats), "automatico")
    return envios


def executar_pos_visita(container, sessoes: SessoesTelegram, horas: float) -> list[tuple[str, str]]:
    """Pergunta "o que achou da visita?" aos clientes cujas visitas já
    aconteceram; devolve [(chat_id, mensagem sem marcadores)]."""
    envios: list[tuple[str, str]] = []
    for enviado in container.pos_visita.enviar_pos_visita(horas):
        texto = separar_midia(enviado.mensagem)[0]
        envios.extend((chat_id, texto) for chat_id in sessoes.chats_do_lead(enviado.lead.id))
    return envios


def pos_visita_do_chat(container, sessoes: SessoesTelegram, chat_id: str) -> str | None:
    """Comando /posvisita: pergunta agora sobre a visita mais recente do cliente."""
    lead_id = sessoes.lead_do_chat(chat_id)
    if not lead_id:
        return None
    enviados = container.pos_visita.enviar_pos_visita(lead_id=lead_id)
    return separar_midia(enviados[0].mensagem)[0] if enviados else None


def followup_do_chat(container, sessoes: SessoesTelegram, chat_id: str) -> str | None:
    """Comando /followup: reengaja AGORA o lead deste chat (demonstração do
    Exemplo 3 sem esperar os minutos de inatividade)."""
    lead_id = sessoes.lead_do_chat(chat_id)
    lead = container.conversation_service.buscar_lead(lead_id) if lead_id else None
    if lead is None or not lead.historico:
        return None
    enviado = container.followup_agent.reengajar(lead)
    _registrar_followup(container, lead.id, True, "manual")
    return enviado.mensagem


def _registrar_followup(container, lead_id: str, telegram: bool, origem: str) -> None:
    store = getattr(container, "evento_store", None)
    if store is not None:
        store.registrar_evento("followup_disparado", {"lead_id": lead_id, "telegram": telegram, "origem": origem})


def boas_vindas_de_volta(container, sessoes: SessoesTelegram, chat_id: str) -> str | None:
    """/start de um cliente JÁ identificado neste chat: cumprimenta e mostra
    todas as agendas ativas dele, com os respectivos corretores. None se o
    chat ainda não tem cliente identificado (aí o fluxo normal pede o CPF)."""
    from src.agents.agendas_cliente import descrever_agendas_do_cliente

    lead_id = sessoes.lead_do_chat(chat_id)
    lead = container.conversation_service.buscar_lead(lead_id) if lead_id else None
    if lead is None or not lead.cliente_identificado:
        return None
    primeiro_nome = (lead.nome or "").split(" ")[0]
    agendas = descrever_agendas_do_cliente(container.agenda_repository, container.corretor_repository, lead.cpf)
    partes = [f"Olá de novo, {primeiro_nome}! 👋"]
    if agendas:
        partes.append(agendas)
    partes.append("Como posso te ajudar agora?")
    return "\n\n".join(partes)


def transcrever_audio(container, audio: bytes) -> str:
    """Nota de voz do Telegram (OGG/Opus) -> texto. Vazio se não entendeu."""
    return container.voz.fala_para_texto(audio, formato="ogg") if container.voz else ""


def fala_da_resposta(container, texto: str) -> bytes:
    """Resposta do Sr. Agim em áudio (OGG/Opus, formato de nota de voz)."""
    if not (container.voz and settings.voz_responder_em_audio):
        return b""
    try:
        return container.voz.texto_para_fala(texto)
    except Exception:  # noqa: BLE001 — sem áudio, o cliente ainda recebe o texto
        _log.exception("Falha ao gerar o áudio da resposta")
        return b""


def fotos_para_enviar(container, midias) -> list[tuple[Path, str]]:
    """Galeria completa em "detalhes", uma capa por imóvel nas sugestões."""
    por_id = {im.id: im for im in container.repositorio_imoveis.listar_todos()}
    fotos: list[tuple[Path, str]] = []
    for tipo, ids in midias:
        for imovel_id in ids:
            im = por_id.get(imovel_id)
            if not im:
                continue
            for caminho in im.fotos if tipo == "FOTOS" else im.fotos[:1]:
                fotos.append((RAIZ / caminho, f"{im.titulo} — {nome_ambiente(caminho)} (ilustrativa)"))
    return [(c, legenda) for c, legenda in fotos if c.exists()][:10]  # limite do álbum no Telegram


def aguardar_conexao_telegram(token: str, tentativas: int = 5, espera_s: int = 10) -> bool:
    """Testa o acesso a api.telegram.org ANTES de iniciar o bot. Sem isso, uma
    oscilação de rede (Wi-Fi, VPN, firewall/antivírus) derrubava o bot com um
    traceback enorme de `TimedOut`."""
    import time

    import httpx

    for tentativa in range(1, tentativas + 1):
        try:
            resposta = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
            if resposta.status_code == 401:
                raise SystemExit("O Telegram recusou o token (401). Confira TELEGRAM_BOT_TOKEN no .env.")
            return True
        except httpx.HTTPError as erro:
            print(f"[telegram] Sem conexão com api.telegram.org ({type(erro).__name__}) — "
                  f"tentativa {tentativa}/{tentativas}; nova tentativa em {espera_s} s...", flush=True)
            time.sleep(espera_s)
    return False


def main() -> None:
    if not settings.telegram_habilitado:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN não configurado no .env. Crie o bot com o @BotFather e cole o token."
        )

    from telegram import InputMediaPhoto, Update
    from telegram.constants import ChatAction
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    from src.container import montar_container

    logging.basicConfig(level=logging.INFO)
    container = montar_container()
    sessoes = SessoesTelegram()

    async def _enviar(update: Update, texto: str, midias) -> None:
        if texto:
            await update.message.reply_text(texto)
        fotos = fotos_para_enviar(container, midias)
        if fotos:
            await update.message.reply_media_group(
                [InputMediaPhoto(c.read_bytes(), caption=legenda) for c, legenda in fotos]
            )

    async def _processar(update: Update, texto: str) -> None:
        chat_id = str(update.effective_chat.id)
        await update.effective_chat.send_action(ChatAction.TYPING)
        try:
            # O grafo chama o LLM (pode levar alguns segundos): roda fora do
            # loop assíncrono para o bot continuar atendendo outros chats.
            resposta, midias = await asyncio.to_thread(responder, container, sessoes, chat_id, texto)
        except Exception:  # noqa: BLE001
            _log.exception("Erro ao processar mensagem do chat %s", chat_id)
            await update.message.reply_text("Desculpe, tive um problema agora. Pode repetir, por favor?")
            return
        await _enviar(update, resposta, midias)

    async def tratar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _processar(update, update.message.text or "")

    async def tratar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Cliente mandou ÁUDIO: transcreve (Azure Speech), responde em texto
        e também em áudio (voz neural em português)."""
        if not container.voz:
            await update.message.reply_text(
                "Por enquanto só consigo ler mensagens de texto 🙂 Pode escrever, por favor?")
            return
        await update.effective_chat.send_action(ChatAction.TYPING)
        midia = update.message.voice or update.message.audio
        arquivo = await midia.get_file()
        audio = bytes(await arquivo.download_as_bytearray())
        try:
            texto = await asyncio.to_thread(transcrever_audio, container, audio)
        except Exception as erro:  # noqa: BLE001
            from src.infrastructure.voice.azure_speech_service import explicar_erro_de_voz

            _log.exception("Falha ao transcrever áudio: %s", explicar_erro_de_voz(erro))
            await update.message.reply_text("Tive um problema para ouvir o áudio agora 😕 Pode escrever, por favor?")
            return
        if not texto:
            await update.message.reply_text("Não consegui entender o áudio 😕 Pode repetir ou escrever, por favor?")
            return
        await update.message.reply_text(f"🎤 Entendi: “{texto}”")
        chat_id = str(update.effective_chat.id)
        try:
            resposta, midias = await asyncio.to_thread(responder, container, sessoes, chat_id, texto)
        except Exception:  # noqa: BLE001
            _log.exception("Erro ao processar áudio do chat %s", chat_id)
            await update.message.reply_text("Desculpe, tive um problema agora. Pode repetir, por favor?")
            return
        await _enviar(update, resposta, midias)
        fala = await asyncio.to_thread(fala_da_resposta, container, resposta)
        if fala:
            await update.message.reply_voice(fala)

    async def comando_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        texto = await asyncio.to_thread(boas_vindas_de_volta, container, sessoes, str(update.effective_chat.id))
        if texto:  # cliente já identificado neste chat: mostra as agendas dele
            await update.message.reply_text(texto)
            return
        await _processar(update, "Olá")

    async def comando_encerrar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _processar(update, "encerrar atendimento")

    async def comando_novo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        sessoes.esquecer(str(update.effective_chat.id))
        await _processar(update, "Olá")

    async def comando_followup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        await update.effective_chat.send_action(ChatAction.TYPING)
        mensagem = await asyncio.to_thread(followup_do_chat, container, sessoes, chat_id)
        if mensagem:
            print(f"[follow-up] disparado manualmente (/followup) para o chat {chat_id}.", flush=True)
            await update.message.reply_text(mensagem)
        else:
            await update.message.reply_text("Ainda não temos conversa neste chat. Envie /start primeiro.")

    async def comando_posvisita(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        mensagem = await asyncio.to_thread(pos_visita_do_chat, container, sessoes, str(update.effective_chat.id))
        await update.message.reply_text(
            mensagem or "Não encontrei visita confirmada sua para perguntar. (Agende e confirme uma visita antes.)")

    async def ciclo_followup(app: Application) -> None:
        intervalo = max(1, settings.followup_intervalo_minutos) * 60
        await asyncio.sleep(20)  # 1ª verificação logo após iniciar (não espera o intervalo inteiro)
        while True:
            try:
                envios = await asyncio.to_thread(executar_followup, container, sessoes, settings.followup_minutos)
                envios += await asyncio.to_thread(executar_pos_visita, container, sessoes, settings.pos_visita_horas)
                for chat_id, mensagem in envios:
                    await app.bot.send_message(chat_id=int(chat_id), text=mensagem)
                print(
                    f"[follow-up/pós-visita] verificação concluída: {len(envios)} mensagem(ns) enviada(s). "
                    f"Próxima em {intervalo // 60} min.",
                    flush=True,
                )
            except Exception:  # noqa: BLE001
                _log.exception("Falha no ciclo de follow-up automático")
            await asyncio.sleep(intervalo)

    async def iniciar_followup(app: Application) -> None:
        # Guarda a referência da tarefa (o asyncio só mantém referência fraca).
        app.bot_data["tarefa_followup"] = asyncio.get_running_loop().create_task(ciclo_followup(app))

    app: Application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        # Redes mais lentas/instáveis: espera mais antes de desistir (padrão é 5 s).
        .connect_timeout(30).read_timeout(30).write_timeout(30).pool_timeout(30)
        .get_updates_connect_timeout(30).get_updates_read_timeout(30)
        .post_init(iniciar_followup)
        .build()
    )
    app.add_handler(CommandHandler("start", comando_start))
    app.add_handler(CommandHandler("novo", comando_novo))
    app.add_handler(CommandHandler("followup", comando_followup))
    app.add_handler(CommandHandler("posvisita", comando_posvisita))
    app.add_handler(CommandHandler("encerrar", comando_encerrar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tratar_mensagem))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, tratar_audio))

    print(f"Bot do Telegram ({settings.agente_nome}) rodando. Pressione Ctrl+C para parar.")
    print(f"LLM ativo: {settings.descricao_llm}")
    print("Voz (Azure Speech): " + (f"ativa — região {settings.azure_speech_region}, voz {settings.azure_speech_voz}"
                                     if container.voz else "desligada (sem AZURE_SPEECH_KEY no .env)"))
    print(
        f"Follow-up automático: leads sem resposta há {settings.followup_minutos} min, "
        f"1ª verificação em ~20 s e depois a cada {settings.followup_intervalo_minutos} min. "
        "(Use /followup no chat para disparar na hora.)"
    )
    if not aguardar_conexao_telegram(settings.telegram_bot_token):
        raise SystemExit(
            "\nNão foi possível conectar ao Telegram (api.telegram.org). O problema é de REDE, não do bot:\n"
            "  • teste no navegador: https://api.telegram.org (deve abrir uma página do Telegram);\n"
            "  • desligue VPN/proxy ou libere o Python no firewall/antivírus;\n"
            "  • em rede corporativa/faculdade o Telegram pode estar bloqueado — tente outra rede ou o 4G do celular.\n"
            "Diagnóstico completo: python scripts/diagnostico_conexao.py"
        )
    app.run_polling()


if __name__ == "__main__":
    main()
