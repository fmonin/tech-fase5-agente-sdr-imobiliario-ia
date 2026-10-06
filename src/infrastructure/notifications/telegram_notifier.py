"""Notificador via Telegram.

Implementa `INotifier`. Usado tanto para o bot conversar com o lead pelo
Telegram (`telegram_bot.py`) quanto para notificar um corretor humano quando
um lead fica "quente".

Ativação:
    Só ativado se `TELEGRAM_BOT_TOKEN` estiver preenchido no .env
    (`settings.telegram_habilitado`). Sem token, o sistema continua
    funcionando normalmente pela interface Streamlit.
"""
from __future__ import annotations

import httpx

from src.config import settings
from src.domain.interfaces import INotifier

_API_BASE = "https://api.telegram.org/bot{token}"


class TelegramNotifier(INotifier):
    def __init__(self, token: str | None = None) -> None:
        self._token = token or settings.telegram_bot_token
        if not self._token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN não foi configurado no arquivo .env.\n"
                "Preencha-o para ativar as notificações via Telegram."
            )

    def enviar(self, destinatario: str, mensagem: str) -> None:
        url = f"{_API_BASE.format(token=self._token)}/sendMessage"
        with httpx.Client(timeout=10) as cliente:
            cliente.post(url, json={"chat_id": destinatario, "text": mensagem})
