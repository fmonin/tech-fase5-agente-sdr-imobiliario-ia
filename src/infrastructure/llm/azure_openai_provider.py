"""Provedor de LLM usando Azure OpenAI.

Implementa `ILLMProvider`. Instanciado quando `[llm] provider` (config/settings.toml) é "azure" ou "auto" e há chave no .env
(veja `src/infrastructure/llm/factory.py`).

Configuração:
    O SDK oficial da OpenAI (`pip install openai`) já suporta Azure OpenAI.
    Apenas muda o cliente (`AzureOpenAI` em vez de `OpenAI`) e como o modelo
    é identificado — usamos o deployment name criado no portal Azure, não o
    nome cru do modelo.
"""
from __future__ import annotations

import json
from typing import Optional

from openai import AzureOpenAI

from src.config import Settings, settings
from src.domain.interfaces import ILLMProvider


class AzureOpenAIProvider(ILLMProvider):
    def __init__(self, cfg: Optional[Settings] = None) -> None:
        cfg = cfg or settings
        self._cliente = AzureOpenAI(
            api_key=cfg.azure_openai_api_key,
            azure_endpoint=cfg.azure_openai_endpoint,
            api_version=cfg.azure_openai_api_version,
        )
        self._deployment = cfg.azure_openai_deployment

    def gerar_resposta(self, mensagens: list[dict], temperatura: float = 0.4) -> str:
        resposta = self._cliente.chat.completions.create(
            model=self._deployment,
            messages=mensagens,
            temperature=temperatura,
        )
        return (resposta.choices[0].message.content or "").strip()

    def extrair_dados_estruturados(self, texto: str, schema_descricao: str) -> dict:
        prompt_sistema = (
            "Você extrai informações estruturadas de conversas entre clientes (leads) "
            "e o atendente de uma imobiliária. Responda SOMENTE com um JSON válido, "
            f"sem markdown, seguindo este formato: {schema_descricao}. "
            "O texto pode conter o 'Histórico da conversa' seguido da 'Última "
            "mensagem do lead'. Considere TUDO o que o lead informou ao longo da "
            "conversa (use as perguntas do atendente para interpretar respostas "
            "curtas, ex.: 'Agente: quantos quartos?' / 'Lead: 3'). Se a última "
            "mensagem contradizer algo anterior, a última mensagem prevalece. "
            "Extraia apenas o que o LEAD disse, nunca sugestões do atendente. "
            "Se uma informação nunca foi informada, use null (nunca string vazia)."
        )
        resposta = self._cliente.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": texto},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        conteudo = resposta.choices[0].message.content or "{}"
        try:
            return json.loads(conteudo)
        except json.JSONDecodeError:
            return {}
