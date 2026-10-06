"""Versões novas do LangGraph proíbem nó com o mesmo nome de uma chave do
estado (ValueError: 'x' is already being used as a state key)."""
import re
from pathlib import Path

from src.agents.state import EstadoConversa


def test_nos_do_grafo_nao_colidem_com_chaves_do_estado():
    nos = re.findall(r'add_node\("(\w+)"', Path("src/agents/graph.py").read_text(encoding="utf-8"))
    assert nos and not set(nos) & set(EstadoConversa.__annotations__)
