"""Busca semântica (RAG) sobre a base de imóveis.

Implementa `IVectorSearch`. Para manter o projeto simples de rodar (sem
depender de um banco vetorial externo como Pinecone/Chroma + custos de
embeddings), usamos TF-IDF + similaridade de cosseno do scikit-learn.
Isso já é o suficiente para demonstrar o conceito de RAG (Retrieval
Augmented Generation): buscar os imóveis mais relevantes para a pergunta
do lead e usá-los como contexto na resposta do agente.

Se quiser evoluir para embeddings "de verdade" (ex.: Azure OpenAI
Embeddings), basta criar outra classe que implemente `IVectorSearch` e
trocar no ponto de montagem (`src/agents/graph.py`) — as camadas
superiores não mudam (Open/Closed Principle).
"""
from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.domain.entities import Imovel
from src.domain.interfaces import IPropertyRepository, IVectorSearch
from src.domain.localizacao import MapaBairros


def representacao_textual(imovel: Imovel, mapa: MapaBairros | None = None) -> str:
    """Texto que representa o imóvel no índice (TF-IDF ou embeddings).

    Inclui a vizinhança do bairro: assim uma busca por "perto da Mooca"
    também encontra imóveis no Tatuapé ou no Ipiranga.
    """
    vizinhos = mapa.vizinhos_de(imovel.bairro) if mapa else []
    perto = f" Fica perto de: {', '.join(vizinhos)}." if vizinhos else ""
    negocio = "à venda" if imovel.tipo_negocio == "venda" else "para alugar"
    return (
        f"{imovel.titulo}. Imóvel {negocio} no bairro {imovel.bairro}, {imovel.zona} de São Paulo."
        f"{perto} {imovel.quartos} quartos, {imovel.metragem:.0f} m². {imovel.descricao}"
        f"{' Indicado para investimento e renda de aluguel.' if imovel.finalidade_investimento else ''}"
    )


class TfidfVectorSearch(IVectorSearch):
    def __init__(self, repositorio: IPropertyRepository, mapa_bairros: MapaBairros | None = None) -> None:
        self._imoveis: list[Imovel] = repositorio.listar_todos()
        self._textos = [representacao_textual(im, mapa_bairros) for im in self._imoveis]
        self._vectorizer = TfidfVectorizer(strip_accents="unicode", lowercase=True)
        self._matriz = (
            self._vectorizer.fit_transform(self._textos) if self._textos else None
        )

    def buscar_similares(self, consulta: str, top_k: int = 3) -> list[Imovel]:
        if self._matriz is None or not consulta.strip():
            return []

        vetor_consulta = self._vectorizer.transform([consulta])
        similaridades = cosine_similarity(vetor_consulta, self._matriz).flatten()

        indices_ordenados = similaridades.argsort()[::-1][:top_k]
        return [
            self._imoveis[i] for i in indices_ordenados if similaridades[i] > 0
        ]
