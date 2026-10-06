"""Voice AI (Azure Speech via REST) sem rede: httpx.MockTransport."""
import io
import json
import wave

import httpx
import numpy as np

from src.infrastructure.voice.azure_speech_service import (
    AzureSpeechService, texto_para_falar, wav_para_16khz_mono,
)


def _servico(registro):
    def responder(req: httpx.Request) -> httpx.Response:
        registro.append(req)
        if "tts.speech" in str(req.url):
            return httpx.Response(200, content=b"OGGDATA")
        return httpx.Response(200, json={"RecognitionStatus": "Success", "DisplayText": "Quero alugar na Mooca."})

    return AzureSpeechService("chave", "eastus", "pt-BR-AntonioNeural",
                              cliente=httpx.Client(transport=httpx.MockTransport(responder)))


def _wav(taxa=48000, canais=2, segundos=0.5):
    n = int(taxa * segundos)
    amostras = (np.sin(np.linspace(0, 440 * 2 * np.pi * segundos, n)) * 8000).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(canais)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(np.repeat(amostras, canais).tobytes())
    return buf.getvalue()


def test_texto_para_fala_e_fala_para_texto():
    reqs = []
    voz = _servico(reqs)
    assert voz.texto_para_fala("Olá! 🏡 **Mooca**\n\n[[LISTA:IM001]]") == b"OGGDATA"
    tts = reqs[-1]
    assert tts.url.host == "eastus.tts.speech.microsoft.com"
    assert tts.headers["X-Microsoft-OutputFormat"] == "ogg-48khz-16bit-mono-opus"
    corpo = tts.content.decode()
    assert "pt-BR-AntonioNeural" in corpo and "Mooca" in corpo and "LISTA" not in corpo and "🏡" not in corpo

    assert voz.fala_para_texto(b"opus", formato="ogg") == "Quero alugar na Mooca."
    assert reqs[-1].headers["Content-Type"] == "audio/ogg; codecs=opus"
    assert reqs[-1].url.params["language"] == "pt-BR"

    voz.fala_para_texto(_wav(), formato="wav")
    assert reqs[-1].headers["Content-Type"].startswith("audio/wav") and "samplerate=16000" in reqs[-1].headers["Content-Type"]


def test_conversao_wav_e_limpeza_do_texto():
    convertido = wav_para_16khz_mono(_wav(48000, 2, 1.0))
    with wave.open(io.BytesIO(convertido)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert abs(w.getnframes() - 16000) < 5
    longo = "Frase. " * 300
    assert texto_para_falar(longo).endswith("Os detalhes estão na mensagem escrita.")
    assert texto_para_falar("Valor: R\\$ 480.000\n1) Apto") == "Valor: R$ 480.000, 1) Apto"


def test_telegram_transcreve_e_responde_em_audio():
    from types import SimpleNamespace

    from telegram_bot import fala_da_resposta, transcrever_audio

    voz = _servico([])
    container = SimpleNamespace(voz=voz)
    assert transcrever_audio(container, b"opus") == "Quero alugar na Mooca."
    assert fala_da_resposta(container, "Encontrei 3 imóveis.") == b"OGGDATA"
    sem_voz = SimpleNamespace(voz=None)
    assert transcrever_audio(sem_voz, b"x") == "" and fala_da_resposta(sem_voz, "x") == b""


def _wav_float32(taxa=48000):
    import struct

    amostras = (np.sin(np.linspace(0, 200 * np.pi, taxa // 2)) * 0.3).astype("<f4")
    dados = amostras.tobytes()
    fmt = struct.pack("<HHIIHH", 3, 1, taxa, taxa * 4, 4, 32)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(dados)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(dados)) + dados)


def test_wav_float_do_navegador_e_mensagens_de_erro():
    from src.infrastructure.voice.azure_speech_service import explicar_erro_de_voz

    with wave.open(io.BytesIO(wav_para_16khz_mono(_wav_float32()))) as w:
        assert (w.getframerate(), w.getnchannels()) == (16000, 1) and abs(w.getnframes() - 8000) < 5
    req = httpx.Request("POST", "https://eastus.stt.speech.microsoft.com/x")
    assert "chave" in explicar_erro_de_voz(httpx.HTTPStatusError("x", request=req, response=httpx.Response(401, request=req)))
    assert "sem conexão" in explicar_erro_de_voz(httpx.ConnectTimeout("x"))
