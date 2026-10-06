"""Fábrica (Factory) do provedor de LLM.

Este é o ÚNICO lugar do projeto que decide "qual implementação concreta de
ILLMProvider usar". O restante do sistema (agentes, serviços) recebe
sempre a interface `ILLMProvider` — nunca sabe (nem precisa saber) se por
trás está o Azure OpenAI ou o Mock.

Isso é o padrão de projeto "Factory" combinado com Dependency Inversion:
trocamos a implementação concreta em um único ponto central.
"""
from __future__ import annotations

from typing import Optional

from src.config import Settings, settings
from src.domain.interfaces import ILLMProvider


def criar_llm_provider(cfg: Optional[Settings] = None) -> ILLMProvider:
    """`cfg` vem do composition root (container); sem ele, usa o global."""
    cfg = cfg or settings
    if cfg.usando_llm_real:
        from src.infrastructure.llm.azure_openai_provider import AzureOpenAIProvider

        return AzureOpenAIProvider(cfg)

    from src.infrastructure.llm.mock_provider import MockLLMProvider

    return MockLLMProvider()
