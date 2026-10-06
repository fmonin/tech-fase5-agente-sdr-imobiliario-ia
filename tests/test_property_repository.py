"""Testes do repositório de imóveis (usa o data/imoveis.json real)."""
from src.infrastructure.repositories.json_property_repository import JsonPropertyRepository


def test_listar_todos_retorna_imoveis():
    repositorio = JsonPropertyRepository("data/imoveis.json")
    imoveis = repositorio.listar_todos()
    assert len(imoveis) > 0


def test_buscar_filtra_por_tipo_negocio():
    repositorio = JsonPropertyRepository("data/imoveis.json")
    alugueis = repositorio.buscar(tipo_negocio="aluguel")
    assert all(im.tipo_negocio == "aluguel" for im in alugueis)


def test_buscar_filtra_por_preco_maximo():
    repositorio = JsonPropertyRepository("data/imoveis.json")
    resultado = repositorio.buscar(preco_max=400000)
    assert all(im.preco <= 400000 for im in resultado)
