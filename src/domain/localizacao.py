"""Geografia simplificada de São Paulo: bairro -> zona e bairros vizinhos.

Usado pelo `ConsultorImoveisAgent` para, quando não houver imóvel no bairro
pedido, sugerir algo PERTO (bairro vizinho ou mesma zona) antes de partir
para outra região da cidade. Ex.: nada na Mooca -> Tatuapé/Ipiranga
(vizinhos) antes de Casa Verde (Zona Norte).

Camada de domínio: só regras, sem I/O (os dados vêm de
`data/bairros_sp.json`, carregados pela infraestrutura).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Optional


def normalizar_nome(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


@dataclass
class MapaBairros:
    zonas: dict[str, str] = field(default_factory=dict)  # nome normalizado -> zona
    vizinhos: dict[str, set[str]] = field(default_factory=dict)  # normalizado -> nomes
    nomes: dict[str, str] = field(default_factory=dict)  # normalizado -> nome bonito

    @classmethod
    def a_partir_de_dict(cls, bairros: dict[str, dict]) -> "MapaBairros":
        mapa = cls()
        for nome, dados in bairros.items():
            chave = normalizar_nome(nome)
            mapa.nomes[chave] = nome
            mapa.zonas[chave] = dados.get("zona", "")
            mapa.vizinhos.setdefault(chave, set())
        for nome, dados in bairros.items():  # vizinhança simétrica
            chave = normalizar_nome(nome)
            for vizinho in dados.get("vizinhos", []):
                chave_v = normalizar_nome(vizinho)
                mapa.nomes.setdefault(chave_v, vizinho)
                mapa.vizinhos[chave].add(mapa.nomes[chave_v])
                mapa.vizinhos.setdefault(chave_v, set()).add(nome)
        return mapa

    def eh_bairro(self, nome: str) -> bool:
        return normalizar_nome(nome) in self.nomes

    def zona_de(self, bairro: str) -> Optional[str]:
        return self.zonas.get(normalizar_nome(bairro)) or None

    def vizinhos_de(self, bairro: str) -> list[str]:
        return sorted(self.vizinhos.get(normalizar_nome(bairro), set()))

    def bairros_da_zona(self, zona: str) -> list[str]:
        alvo = normalizar_nome(zona)
        return sorted(self.nomes[c] for c, z in self.zonas.items() if normalizar_nome(z) == alvo)

    def vizinhos_da_zona(self, zona: str) -> list[str]:
        """Bairros de OUTRAS zonas que fazem divisa com a zona (ex.: Zona Leste -> Ipiranga, Cambuci...)."""
        de_dentro = set(self.bairros_da_zona(zona))
        return sorted({v for b in de_dentro for v in self.vizinhos_de(b)} - de_dentro)

    def nome_oficial(self, bairro: str) -> str:
        return self.nomes.get(normalizar_nome(bairro), bairro)
