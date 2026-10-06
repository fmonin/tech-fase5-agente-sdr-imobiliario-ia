"""RAG com embeddings (busca por SIGNIFICADO, não só por palavras).

Diferença para o TF-IDF (`vector_store.py`): o TF-IDF compara PALAVRAS
("apê" não casa com "apartamento"); embeddings comparam SIGNIFICADO
("perto do metrô", "bom para família", "para ter renda de aluguel"...).

Duas formas de gerar os embeddings (escolhidas no .env, veja `factory.py`):
    - `SentenceTransformerEmbedder` (PADRÃO): biblioteca sentence-transformers,
      roda LOCALMENTE, de graça, sem chave e sem Azure. Usa um modelo
      multilíngue (bom em português), baixado uma única vez do Hugging Face.
    - `AzureOpenAIEmbedder` (opcional): Azure OpenAI Embeddings.

Como funciona:
    1. Na inicialização, cada imóvel vira um texto (`representacao_textual`,
       que inclui bairro, zona e bairros vizinhos) e um vetor. Os vetores
       ficam num cache em disco (`data/embeddings_cache.json`), então só
       imóveis novos/alterados são recalculados.
    2. Em cada busca, a consulta do lead vira um vetor; comparamos por
       similaridade de cosseno.
    3. Se algo falhar (biblioteca não instalada, modelo não baixado, Azure
       fora do ar), usa o TF-IDF como reserva — o agente nunca para.

Implementa `IVectorSearch`: trocar a forma de busca não muda nenhum agente
(Open/Closed + Dependency Inversion).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional, Protocol

import numpy as np

from src.domain.entities import Imovel
from src.domain.interfaces import IPropertyRepository, IVectorSearch
from src.domain.localizacao import MapaBairros
from src.infrastructure.rag.vector_store import representacao_textual

_log = logging.getLogger(__name__)

MODELO_LOCAL_PADRAO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class IEmbedder(Protocol):
    nome: str  # identifica o modelo (entra na chave do cache)

    def gerar(self, textos: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Embeddings locais com sentence-transformers (sem custo, sem rede após o
    primeiro download do modelo, que fica no cache do Hugging Face)."""

    def __init__(self, modelo: str = MODELO_LOCAL_PADRAO) -> None:
        from sentence_transformers import SentenceTransformer  # import tardio: só se for usado

        self.nome = f"local:{modelo}"
        self._modelo = SentenceTransformer(modelo)

    def gerar(self, textos: list[str]) -> list[list[float]]:
        return self._modelo.encode(textos, normalize_embeddings=True).tolist()


class AzureOpenAIEmbedder:
    def __init__(self, cliente, deployment: str) -> None:  # cliente: openai.AzureOpenAI
        self.nome = f"azure:{deployment}"
        self._cliente = cliente
        self._deployment = deployment

    def gerar(self, textos: list[str]) -> list[list[float]]:
        resposta = self._cliente.embeddings.create(model=self._deployment, input=textos)
        return [item.embedding for item in resposta.data]


class EmbeddingVectorSearch(IVectorSearch):
    def __init__(
        self,
        repositorio: IPropertyRepository,
        embedder: IEmbedder,
        caminho_cache: str | Path = "data/embeddings_cache.json",
        mapa_bairros: MapaBairros | None = None,
        reserva: Optional[IVectorSearch] = None,
    ) -> None:
        self._embedder = embedder
        self._caminho_cache = Path(caminho_cache)
        self._reserva = reserva
        self._imoveis: list[Imovel] = repositorio.listar_todos()
        self._matriz: Optional[np.ndarray] = None
        try:
            textos = [representacao_textual(im, mapa_bairros) for im in self._imoveis]
            self._matriz = self._normalizar(np.array(self._embeddings_com_cache(textos)))
            _log.info("RAG com embeddings ativo (%s, %s imóveis).", embedder.nome, len(self._imoveis))
        except Exception as erro:  # noqa: BLE001 — qualquer falha cai no TF-IDF
            _log.warning("Embeddings indisponíveis (%s). Usando TF-IDF como reserva.", erro)

    @property
    def ativo(self) -> bool:
        return self._matriz is not None

    def buscar_similares(self, consulta: str, top_k: int = 3) -> list[Imovel]:
        if not consulta.strip() or not self._imoveis:
            return []
        if self._matriz is None:
            return self._reserva.buscar_similares(consulta, top_k) if self._reserva else []
        try:
            vetor = self._normalizar(np.array(self._embedder.gerar([consulta])))[0]
        except Exception as erro:  # noqa: BLE001
            _log.warning("Falha ao gerar embedding da consulta (%s). Usando reserva.", erro)
            return self._reserva.buscar_similares(consulta, top_k) if self._reserva else []
        similaridades = self._matriz @ vetor
        ordem = similaridades.argsort()[::-1][:top_k]
        return [self._imoveis[i] for i in ordem]

    # -- internos -------------------------------------------------------------
    def _chave(self, texto: str) -> str:
        return hashlib.sha1(f"{self._embedder.nome}|{texto}".encode("utf-8")).hexdigest()

    def _embeddings_com_cache(self, textos: list[str]) -> list[list[float]]:
        cache: dict[str, list[float]] = {}
        if self._caminho_cache.exists():
            try:
                cache = json.loads(self._caminho_cache.read_text(encoding="utf-8"))
            except ValueError:
                cache = {}
        faltando = [t for t in textos if self._chave(t) not in cache]
        if faltando:
            for texto, vetor in zip(faltando, self._embedder.gerar(faltando)):
                cache[self._chave(texto)] = [float(x) for x in vetor]
            self._caminho_cache.parent.mkdir(parents=True, exist_ok=True)
            self._caminho_cache.write_text(json.dumps(cache), encoding="utf-8")
        return [cache[self._chave(t)] for t in textos]

    @staticmethod
    def _normalizar(matriz: np.ndarray) -> np.ndarray:
        normas = np.linalg.norm(matriz, axis=1, keepdims=True)
        normas[normas == 0] = 1
        return matriz / normas
