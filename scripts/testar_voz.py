"""Testa o Azure Speech configurado (chave no .env, região em config/settings.toml).

    python scripts/testar_voz.py

1. Texto -> fala: gera data/teste_voz.mp3 (abra para ouvir o Sr. Agim).
2. Ida e volta: a fala gerada (OGG, formato do Telegram) é transcrita de novo.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402
from src.infrastructure.voice.azure_speech_service import (  # noqa: E402
    FORMATO_NAVEGADOR, FORMATO_TELEGRAM, criar_servico_de_voz,
)

FRASE = "Olá! Eu sou o Sr. Agim. Encontrei três apartamentos na Mooca que combinam com o que você procura."


def main() -> None:
    voz = criar_servico_de_voz(settings)
    if voz is None:
        raise SystemExit("AZURE_SPEECH_KEY não configurada no .env.")
    print(f"Região: {settings.azure_speech_region} · voz: {settings.azure_speech_voz}")
    mp3 = voz.texto_para_fala(FRASE, formato=FORMATO_NAVEGADOR)
    destino = Path("data/teste_voz.mp3")
    destino.write_bytes(mp3)
    print(f"1) Texto -> fala OK: {len(mp3):,} bytes salvos em {destino}")
    ogg = voz.texto_para_fala(FRASE, formato=FORMATO_TELEGRAM)
    print(f"2) Fala -> texto: “{voz.fala_para_texto(ogg, formato='ogg')}”")
    print("Voz funcionando! ✅")


if __name__ == "__main__":
    main()
