"""Interface Streamlit do Sr. Agim (Agente SDR Imobiliário).

Requisito do desafio: "Interface de fácil navegação". Escolhemos Streamlit
porque é Python puro (sem precisar aprender HTML/JS/React) e roda com um
único comando — ótimo para quem está começando.

A interface tem três telas, escolhidas em um menu simples na barra lateral:

1. "Conversar com o Agente" — simula o chat do lead com o Sr. Agim
   (inclui o cadastro/identificação do cliente, primeiro passo do fluxo).
2. "Painel da Imobiliária" — visão do GESTOR (dashboard): KPIs, funil da
   operação, "atenção agora", demanda × oferta, ranking dos corretores,
   desempenho do agente de IA, leads com etapa/corretor e resumos.
3. "Área do Corretor" — tela para o CORRETOR (não o cliente) consultar a
   própria agenda em linguagem natural, via LLM (`ConsultaAgendaAgent`).
   O corretor só escolhe o próprio nome numa lista (sem senha — é uma POC).

Organização: cada tela fica no seu módulo em `src/interface/paginas/`
(`chat.py`, `painel.py`, `corretor.py`, mais `comum.py` com as peças
compartilhadas); este arquivo só configura a página, monta o container e
faz a navegação.

Importante (separação de responsabilidades): a interface só sabe desenhar
a tela e reagir a cliques. Toda a lógica de negócio vive em
`ConversationService` / `DashboardService` (camada de serviços) — a
interface poderia ser trocada por Flask, FastAPI+React, ou o bot do
Telegram sem precisar tocar no restante do sistema.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st  # noqa: E402

from src.config import settings  # noqa: E402
from src.container import montar_container  # noqa: E402
from src.interface.paginas.chat import pagina_chat  # noqa: E402
from src.interface.paginas.corretor import pagina_area_corretor  # noqa: E402
from src.interface.paginas.painel import pagina_dashboard  # noqa: E402

st.set_page_config(page_title=f"{settings.agente_nome} — Agente Imobiliário", page_icon="🏠", layout="wide")


@st.cache_resource
def obter_container():
    """Cria o container de dependências uma única vez por processo
    (st.cache_resource evita recriar conexões/objetos a cada interação)."""
    return montar_container()


def main() -> None:
    container = obter_container()

    st.sidebar.title("🏠 Navegação")
    pagina = st.sidebar.radio(
        "Escolha a tela:",
        ["Conversar com o Agente", "Painel da Imobiliária", "Área do Corretor"],
        label_visibility="collapsed",
    )
    st.sidebar.divider()
    st.sidebar.caption(
        f"{'🟢' if settings.usando_llm_real else '🟡'} LLM ativo: **{settings.descricao_llm}**  \n"
        + (f"🎤 Voz: **Azure Speech ({settings.azure_speech_region})**" if container.voz else "🔇 Voz: desligada")
    )

    if pagina == "Conversar com o Agente":
        pagina_chat(container)
    elif pagina == "Painel da Imobiliária":
        pagina_dashboard(container)
    else:
        pagina_area_corretor(container)


if __name__ == "__main__":
    main()
