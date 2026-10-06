"""Diagnóstico de conexão com os serviços externos do projeto.

    python scripts/diagnostico_conexao.py

Testa, com a configuração atual (config/settings.toml + .env):
  • Telegram (api.telegram.org)
  • Azure OpenAI (endpoint configurado)
  • Azure Speech (texto -> fala e fala -> texto)
e explica o que fazer em cada tipo de erro.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from src.config import settings  # noqa: E402

DICAS = {
    "timeout": "sem resposta da rede — VPN/proxy/firewall/antivírus bloqueando o Python, ou rede que bloqueia o serviço",
    401: "chave/token inválido — confira o .env (e se a chave é do recurso/região certos)",
    403: "acesso negado — chave sem permissão ou recurso desativado",
    404: "endereço/região errados — confira config/settings.toml",
}


def _testar(nome: str, funcao) -> None:
    try:
        detalhe = funcao()
        print(f"✅ {nome}: OK {detalhe or ''}")
    except httpx.TimeoutException as e:
        print(f"❌ {nome}: {type(e).__name__} → {DICAS['timeout']}")
    except httpx.ConnectError as e:
        print(f"❌ {nome}: não conectou ({e}) → {DICAS['timeout']}")
    except httpx.HTTPStatusError as e:
        codigo = e.response.status_code
        print(f"❌ {nome}: HTTP {codigo} → {DICAS.get(codigo, e.response.text[:200])}")
    except Exception as e:  # noqa: BLE001
        print(f"❌ {nome}: {type(e).__name__}: {e}")


def telegram() -> str:
    if not settings.telegram_bot_token:
        return "(sem TELEGRAM_BOT_TOKEN — ignorado)"
    r = httpx.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe", timeout=20)
    r.raise_for_status()
    return f"— bot @{r.json()['result']['username']}"


def azure_openai() -> str:
    if not settings.azure_openai_api_key:
        return "(sem AZURE_OPENAI_API_KEY — usando Mock)"
    url = (f"{settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/{settings.azure_openai_deployment}"
           f"/chat/completions?api-version={settings.azure_openai_api_version}")
    r = httpx.post(url, headers={"api-key": settings.azure_openai_api_key}, timeout=30,
                   json={"messages": [{"role": "user", "content": "Responda só: ok"}], "max_tokens": 5})
    r.raise_for_status()
    return f"— {settings.azure_openai_deployment}"


def azure_speech() -> str:
    from src.infrastructure.voice.azure_speech_service import FORMATO_TELEGRAM, criar_servico_de_voz

    voz = criar_servico_de_voz(settings)
    if voz is None:
        return "(sem AZURE_SPEECH_KEY — voz desligada)"
    audio = voz.texto_para_fala("Olá, eu sou o Sr. Agim.", formato=FORMATO_TELEGRAM)
    texto = voz.fala_para_texto(audio, formato="ogg")
    return f"— região {settings.azure_speech_region}; ida e volta: “{texto}”"


if __name__ == "__main__":
    _testar("Telegram", telegram)
    _testar("Azure OpenAI", azure_openai)
    _testar("Azure Speech", azure_speech)
