"""Voice AI com o Azure AI Speech (Serviços de Fala) — via API REST.

Implementa `IVoiceService`:
    • fala_para_texto: áudio do cliente (nota de voz do Telegram em OGG/Opus
      ou gravação do microfone no Streamlit em WAV) -> texto em pt-BR;
    • texto_para_fala: resposta do Sr. Agim -> áudio com voz neural em
      português (OGG/Opus para o Telegram, MP3 para o navegador).

Por que REST e não o SDK nativo? O REST aceita OGG/Opus direto (formato das
notas de voz do Telegram), não precisa de bibliotecas nativas nem de ffmpeg
e funciona igual no Windows, Linux e Docker — só usa o `httpx`, que o
projeto já tem.

Ativação: `AZURE_SPEECH_KEY` no .env + `[azure_speech] region` em
config/settings.toml (`settings.voice_habilitado`). Sem chave, o sistema
continua só com texto.
"""
from __future__ import annotations

import io
import re
import wave
from typing import Optional
from xml.sax.saxutils import escape

import httpx

from src.domain.interfaces import IVoiceService
from src.domain.midia import remover_marcadores

FORMATO_TELEGRAM = "ogg-48khz-16bit-mono-opus"
FORMATO_NAVEGADOR = "audio-24khz-48kbitrate-mono-mp3"
_LIMITE_FALA = 700  # caracteres: respostas longas (listas) viram um resumo falado

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def texto_para_falar(texto: str) -> str:
    """Limpa o texto para soar natural: sem marcadores, markdown, emojis e
    cifrões de LaTeX; listas longas são cortadas com um aviso."""
    t = remover_marcadores(texto)
    t = _EMOJI.sub("", t)
    t = re.sub(r"[*_`#>|]", "", t).replace("\\$", "$")
    t = re.sub(r"R\$\s?", "R$ ", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = re.sub(r"\s*\n\s*", ", ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,")
    if len(t) > _LIMITE_FALA:
        corte = t.rfind(".", 0, _LIMITE_FALA)
        t = t[: corte + 1 if corte > 200 else _LIMITE_FALA] + " Os detalhes estão na mensagem escrita."
    return t


def _ler_wav(audio_wav: bytes):
    """Lê um WAV PCM (8/16/24/32 bits) ou IEEE float (32 bits) — o navegador
    pode gravar em qualquer um deles. Devolve (amostras float em escala
    int16, canais, taxa)."""
    import struct

    import numpy as np

    if audio_wav[:4] != b"RIFF" or audio_wav[8:12] != b"WAVE":
        raise ValueError("O áudio gravado não está em WAV.")
    pos, fmt, dados = 12, None, None
    while pos + 8 <= len(audio_wav):
        nome, tamanho = audio_wav[pos:pos + 4], struct.unpack("<I", audio_wav[pos + 4:pos + 8])[0]
        corpo = audio_wav[pos + 8:pos + 8 + tamanho]
        if nome == b"fmt ":
            fmt = list(struct.unpack("<HHIIHH", corpo[:16]))
            if fmt[0] == 0xFFFE and len(corpo) >= 26:  # WAVE_FORMAT_EXTENSIBLE: formato real no SubFormat
                fmt[0] = struct.unpack("<H", corpo[24:26])[0]
        elif nome == b"data":
            dados = corpo
        pos += 8 + tamanho + (tamanho % 2)
    if fmt is None or dados is None:
        raise ValueError("WAV sem cabeçalho/dados.")
    formato, canais, taxa, _, _, bits = fmt
    if formato == 3:
        amostras = np.frombuffer(dados[: len(dados) // 4 * 4], dtype="<f4") * 32767.0
    elif bits == 16:
        amostras = np.frombuffer(dados[: len(dados) // 2 * 2], dtype="<i2").astype(np.float32)
    elif bits == 32:
        amostras = np.frombuffer(dados[: len(dados) // 4 * 4], dtype="<i4").astype(np.float32) / 65536.0
    elif bits == 24:
        brutos = np.frombuffer(dados[: len(dados) // 3 * 3], dtype=np.uint8).reshape(-1, 3)
        inteiros = (brutos[:, 0].astype(np.int32) | (brutos[:, 1].astype(np.int32) << 8)
                    | (brutos[:, 2].astype(np.int32) << 16))
        inteiros = np.where(inteiros >= 1 << 23, inteiros - (1 << 24), inteiros)
        amostras = inteiros.astype(np.float32) / 256.0
    elif bits == 8:
        amostras = (np.frombuffer(dados, dtype=np.uint8).astype(np.float32) - 128.0) * 256.0
    else:
        raise ValueError(f"WAV com {bits} bits não suportado.")
    return amostras, max(1, canais), taxa


def wav_para_16khz_mono(audio_wav: bytes) -> bytes:
    """Converte uma gravação WAV qualquer (ex.: 48 kHz estéreo, float, do
    navegador) para PCM 16 kHz mono 16 bits, formato aceito pela API de fala."""
    import numpy as np

    amostras, canais, taxa = _ler_wav(audio_wav)
    if canais > 1:
        amostras = amostras[: len(amostras) // canais * canais].reshape(-1, canais).mean(axis=1)
    if taxa != 16000 and len(amostras):
        destino = np.linspace(0, len(amostras) - 1, int(len(amostras) * 16000 / taxa))
        amostras = np.interp(destino, np.arange(len(amostras)), amostras)
    saida = io.BytesIO()
    with wave.open(saida, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.clip(amostras, -32768, 32767).astype(np.int16).tobytes())
    return saida.getvalue()


def explicar_erro_de_voz(erro: Exception) -> str:
    """Mensagem amigável para mostrar na tela/terminal."""
    if isinstance(erro, httpx.TimeoutException) or isinstance(erro, httpx.ConnectError):
        return ("sem conexão com o Azure Speech (rede/VPN/firewall). Rode `python scripts/diagnostico_conexao.py`.")
    if isinstance(erro, httpx.HTTPStatusError):
        codigo = erro.response.status_code
        return {401: "chave do Azure Speech inválida (confira AZURE_SPEECH_KEY no .env)",
                403: "acesso negado pelo Azure Speech (chave/recurso)",
                404: "região errada (confira [azure_speech] region em config/settings.toml)"}.get(
            codigo, f"o Azure Speech respondeu HTTP {codigo}")
    return f"{type(erro).__name__}: {erro}"


class AzureSpeechService(IVoiceService):
    def __init__(self, chave: str, regiao: str, voz: str = "pt-BR-AntonioNeural",
                 cliente: Optional[httpx.Client] = None) -> None:
        if not chave:
            raise ValueError("AZURE_SPEECH_KEY não configurada no .env.")
        self._chave = chave
        self._regiao = regiao
        self._voz = voz
        self._cliente = cliente or httpx.Client(timeout=30)

    def texto_para_fala(self, texto: str, formato: str = FORMATO_TELEGRAM) -> bytes:
        fala = texto_para_falar(texto)
        if not fala:
            return b""
        ssml = (f"<speak version='1.0' xml:lang='pt-BR'><voice name='{self._voz}'>"
                f"{escape(fala)}</voice></speak>")
        resposta = self._cliente.post(
            f"https://{self._regiao}.tts.speech.microsoft.com/cognitiveservices/v1",
            content=ssml.encode("utf-8"),
            headers={
                "Ocp-Apim-Subscription-Key": self._chave,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": formato,
                "User-Agent": "agente-sdr-imobiliario",
            },
        )
        resposta.raise_for_status()
        return resposta.content

    def fala_para_texto(self, audio_bytes: bytes, formato: str = "ogg") -> str:
        """`formato`: "ogg" (Opus — nota de voz do Telegram) ou "wav"."""
        if formato == "wav":
            audio_bytes = wav_para_16khz_mono(audio_bytes)
            tipo = "audio/wav; codecs=audio/pcm; samplerate=16000"
        else:
            tipo = "audio/ogg; codecs=opus"
        resposta = self._cliente.post(
            f"https://{self._regiao}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1",
            params={"language": "pt-BR", "format": "simple"},
            content=audio_bytes,
            headers={"Ocp-Apim-Subscription-Key": self._chave, "Content-Type": tipo, "Accept": "application/json"},
        )
        resposta.raise_for_status()
        dados = resposta.json()
        return (dados.get("DisplayText") or "").strip() if dados.get("RecognitionStatus") == "Success" else ""


def criar_servico_de_voz(settings) -> Optional[IVoiceService]:
    """Fábrica: o serviço de voz só existe quando há chave configurada."""
    if not settings.voice_habilitado:
        return None
    return AzureSpeechService(settings.azure_speech_key, settings.azure_speech_region, settings.azure_speech_voz)
