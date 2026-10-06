"""Fotos de imóveis dentro das mensagens do agente.

A resposta do agente continua sendo TEXTO (é o que fica salvo no histórico e
vai para o LLM), mas pode carregar um marcador que a interface troca pelas
imagens:
    [[FOTOS:IM012]]          -> galeria completa de um imóvel (detalhes)
    [[CAPAS:IM007,IM014]]    -> foto de capa de cada imóvel sugerido
    [[LISTA:...]] / [[AGENDA:...]] -> não exibidos (guardam a ordem da lista
                                     e a pergunta pendente sobre a agenda)

Assim o Streamlit e o Telegram decidem COMO mostrar as fotos, e os agentes
não dependem de nenhuma biblioteca de interface.
"""
from __future__ import annotations

import re

_PADRAO = re.compile(r"\s*\[\[(FOTOS|CAPAS|LISTA|AGENDA|POSVISITA|ENCERRADO):([A-Za-z0-9_,\-]+)\]\]")


def anexar_fotos(texto: str, imovel_id: str) -> str:
    return f"{texto}\n\n[[FOTOS:{imovel_id}]]"


def anexar_capas(texto: str, ids: list[str]) -> str:
    return f"{texto}\n\n[[CAPAS:{','.join(ids)}]]" if ids else texto


def anexar_lista(texto: str, ids: list[str]) -> str:
    """Marca (sem exibir) a ordem dos imóveis listados, para o lead poder
    responder "fotos do 2"."""
    return f"{texto}\n\n[[LISTA:{','.join(ids)}]]" if ids else texto


def separar_midia(texto: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """('texto sem marcadores', [('FOTOS', ['IM012']), ('CAPAS', [...])]).
    O marcador LISTA não gera mídia (só guarda a ordem da lista)."""
    midias = [
        (tipo, [i for i in ids.split(",") if i]) for tipo, ids in _PADRAO.findall(texto) if tipo not in ("LISTA", "AGENDA", "POSVISITA", "ENCERRADO")
    ]
    return _PADRAO.sub("", texto).strip(), midias


def remover_marcadores(texto: str) -> str:
    return _PADRAO.sub("", texto).strip()


def nome_ambiente(caminho_foto: str) -> str:
    """'data/imagens/IM012/2_sala.jpg' -> 'Sala de estar'."""
    nomes = {
        "fachada": "Fachada", "sala": "Sala de estar", "cozinha": "Cozinha", "quarto": "Quarto",
        "banheiro": "Banheiro", "varanda": "Varanda", "area_lazer": "Área de lazer",
        "terraco": "Terraço", "ambiente_integrado": "Ambiente integrado",
        "sala_comercial": "Sala comercial", "recepcao": "Recepção",
    }
    arquivo = caminho_foto.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    chave = arquivo.split("_", 1)[1] if "_" in arquivo else arquivo
    return nomes.get(chave, chave.replace("_", " ").capitalize())
