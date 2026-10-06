"""Escolhe a implementação de RAG conforme `[embeddings] provider` em config/settings.toml.

- `local` (padrão): sentence-transformers no próprio computador — grátis,
  sem chave. Se a biblioteca não estiver instalada, cai no TF-IDF.
- `azure`: Azure OpenAI Embeddings (`AZURE_OPENAI_EMBEDDING_DEPLOYMENT`).
- `tfidf`: só o TF-IDF (mais leve; bom para testes/CI).
Em todos os casos o TF-IDF fica de reserva se os embeddings falharem.
"""
from __future__ import annotations

import logging

from src.config import settings
from src.domain.interfaces import IPropertyRepository, IVectorSearch
from src.domain.localizacao import MapaBairros
from src.infrastructure.rag.vector_store import TfidfVectorSearch

_log = logging.getLogger(__name__)


def criar_busca_semantica(
    repositorio: IPropertyRepository, mapa_bairros: MapaBairros | None = None
) -> IVectorSearch:
    tfidf = TfidfVectorSearch(repositorio, mapa_bairros)
    provedor = settings.embeddings_provider

    try:
        if provedor == "local":
            from src.infrastructure.rag.embedding_search import SentenceTransformerEmbedder

            embedder = SentenceTransformerEmbedder(settings.embeddings_modelo_local)
        elif provedor == "azure" and settings.azure_openai_embedding_deployment:
            from openai import AzureOpenAI

            from src.infrastructure.rag.embedding_search import AzureOpenAIEmbedder

            cliente = AzureOpenAI(
                api_key=settings.azure_openai_api_key,
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
            )
            embedder = AzureOpenAIEmbedder(cliente, settings.azure_openai_embedding_deployment)
        else:
            return tfidf
    except Exception as erro:  # noqa: BLE001 — ex.: sentence-transformers não instalado
        _log.warning("Embeddings '%s' indisponíveis (%s). Usando TF-IDF.", provedor, erro)
        return tfidf

    from src.infrastructure.rag.embedding_search import EmbeddingVectorSearch

    return EmbeddingVectorSearch(
        repositorio,
        embedder,
        caminho_cache=settings.embeddings_cache_path,
        mapa_bairros=mapa_bairros,
        reserva=tfidf,
    )
