"""Peças de tela compartilhadas pelas páginas (formatação segura, exibição
de mensagens com fotos, resumo do lead, datas)."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.domain.investimento import formatar_moeda
from src.domain.midia import nome_ambiente, separar_midia

_RAIZ_PROJETO = Path(__file__).resolve().parents[3]


def _texto_seguro(texto: str) -> str:
    """Escapa o "$" antes de exibir no Streamlit.

    O `st.markdown`/`st.write` interpreta texto entre dois "$" como fórmula
    matemática (LaTeX). Numa frase com dois valores em reais (ex.: "R$ 480.000
    ... R$ 24.000"), tudo entre os dois "$" virava uma fórmula em itálico e
    sem espaços. Com "\\$" o Streamlit mostra o cifrão normalmente.
    """
    return str(texto).replace("\\$", "$").replace("$", "\\$")


@st.cache_data
def _creditos_fotos() -> dict:
    """Autor/licença das fotos baixadas de banco livre (scripts/baixar_fotos_imoveis.py)."""
    import json

    arquivo = _RAIZ_PROJETO / "data" / "imagens" / "creditos.json"
    try:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _legenda(caminho: str) -> str:
    credito = _creditos_fotos().get(caminho)
    if credito:
        return f"{nome_ambiente(caminho)} · Foto: {credito['autor'][:40]} ({credito['licenca']}) — ilustrativa"
    return f"{nome_ambiente(caminho)} · imagem ilustrativa"


def _mostrar_mensagem(container, texto: str) -> None:
    """Mostra o texto do agente e, se houver marcadores, as fotos dos imóveis:
    [[FOTOS:ID]] -> galeria do imóvel; [[CAPAS:ID1,ID2]] -> capa de cada sugerido."""
    texto_limpo, midias = separar_midia(texto)
    # quebra de linha "dura" para a lista de imóveis não virar um parágrafo só
    st.markdown(_texto_seguro(texto_limpo).replace("\n", "  \n"))
    if not midias:
        return
    por_id = {im.id: im for im in container.repositorio_imoveis.listar_todos()}
    for tipo, ids in midias:
        imoveis = [por_id[i] for i in ids if i in por_id]
        if tipo == "FOTOS":
            for im in imoveis:
                fotos = [f for f in im.fotos if (_RAIZ_PROJETO / f).exists()]
                st.caption(f"📷 Fotos — {im.titulo} ({im.bairro})")
                for inicio in range(0, len(fotos), 3):
                    colunas = st.columns(3)
                    for coluna, foto in zip(colunas, fotos[inicio:inicio + 3]):
                        coluna.image(str(_RAIZ_PROJETO / foto), caption=_legenda(foto), use_container_width=True)
        else:  # CAPAS
            colunas = st.columns(max(1, min(3, len(imoveis))))
            for coluna, im in zip(colunas, imoveis[:3]):
                capa = next((f for f in im.fotos if (_RAIZ_PROJETO / f).exists()), None)
                if capa:
                    coluna.image(str(_RAIZ_PROJETO / capa), use_container_width=True)
                coluna.markdown(_texto_seguro(f"**{im.bairro}** · {im.quartos} qto(s) · {im.metragem:.0f} m²  \n{formatar_moeda(im.preco)}"))
            st.caption("Quer ver todas as fotos de algum deles? É só pedir citando o bairro ou o tipo do imóvel.")


def _quando(agendamento) -> str:
    return agendamento.data_hora.strftime("%d/%m/%Y às %Hh%M") if agendamento.data_hora else agendamento.quando_sugerido


def _secao_resumo_do_lead(container, lead, chave: str) -> None:
    """Resumo inteligente do lead para o corretor + botão para gerar/atualizar."""
    if lead.agendamentos:
        ultimo = lead.agendamentos[-1]
        st.markdown(_texto_seguro(f"**Agendamento:** {ultimo.quando_sugerido} ({ultimo.status})"))
    if lead.resumo_corretor:
        st.markdown(_texto_seguro(f"📝 **Resumo para o corretor:** {lead.resumo_corretor}"))
    else:
        st.caption("Ainda sem resumo para este lead.")
    rotulo = "🔄 Atualizar resumo" if lead.resumo_corretor else "📝 Gerar resumo com IA"
    if st.button(rotulo, key=f"resumo_{chave}"):
        with st.spinner("Gerando resumo..."):
            container.conversation_service.gerar_resumo(lead.id)
        st.rerun()
