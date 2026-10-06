"""Sessões do bot do Telegram: qual lead cada chat está usando.

Mapa chat_id -> lead_id salvo em `data/telegram_sessoes.json` (sobrevive a
reinícios do bot). Também permite o caminho inverso (lead -> chats), usado
pelo follow-up automático para mandar a mensagem no chat certo.
"""
from __future__ import annotations

import json
from pathlib import Path

ARQUIVO_SESSOES = Path(__file__).resolve().parents[3] / "data" / "telegram_sessoes.json"


class SessoesTelegram:
    """Mapa chat_id -> lead_id, salvo em JSON."""

    def __init__(self, arquivo: Path = ARQUIVO_SESSOES) -> None:
        self._arquivo = arquivo
        try:
            self._mapa: dict[str, str] = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._mapa = {}

    def lead_do_chat(self, chat_id: str) -> str | None:
        return self._mapa.get(chat_id)

    def associar(self, chat_id: str, lead_id: str) -> None:
        if self._mapa.get(chat_id) != lead_id:
            self._mapa[chat_id] = lead_id
            self._salvar()

    def esquecer(self, chat_id: str) -> None:
        if self._mapa.pop(chat_id, None) is not None:
            self._salvar()

    def _salvar(self) -> None:
        self._arquivo.parent.mkdir(parents=True, exist_ok=True)
        self._arquivo.write_text(json.dumps(self._mapa, indent=1), encoding="utf-8")

    def chats_do_lead(self, lead_id: str) -> list[str]:
        return [chat for chat, lead in self._mapa.items() if lead == lead_id]
