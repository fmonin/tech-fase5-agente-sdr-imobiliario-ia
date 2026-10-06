"""Agente de Detalhes do Imóvel.

Responsabilidade única: quando o lead pede mais informações sobre UM imóvel
("quero mais informação do imóvel do Tatuapé"), apresentar a ficha completa
dele — em vez de refazer a busca e repetir as sugestões.

Os números (preço por m², diferença para o orçamento, comparação com a média
do bairro/cidade no Índice FipeZAP, análise de investimento) são calculados
em Python; o LLM só redige a resposta com esses dados.
"""
from __future__ import annotations

from src.agents.contexto_conversa import historico_anterior
from src.agents.foco_imovel import candidatos_citados, ids_mostrados, numero_da_lista, resolver_imovel_citado
from src.agents.state import EstadoConversa
from src.config import settings
from src.domain.entities import Imovel
from src.domain.interfaces import ILLMProvider, IMarketDataRepository, IPropertyRepository
from src.domain.investimento import (
    INDICADORES_PADRAO,
    analisar_investimento,
    formatar_moeda,
    formatar_percentual,
)
from src.domain.localizacao import MapaBairros
from src.domain.midia import anexar_capas, anexar_fotos, anexar_lista

_PROMPT = (
    f"Você é {settings.agente_nome}, um agente imobiliário (SDR) humanizado. O lead "
    "pediu mais informações sobre UM imóvel específico. Apresente a ficha abaixo "
    "de forma clara e simpática (pode usar uma lista curta com os pontos principais), "
    "respondendo primeiro ao que ele perguntou. Use SOMENTE os dados da ficha — "
    "nunca invente características (vagas, andar, condomínio etc.) que não estejam "
    "nela; se ele perguntar algo que não está na ficha, diga que o corretor confirma "
    "na visita. Se o imóvel estiver acima do orçamento, diga isso com naturalidade. "
    "Não cumprimente de novo e não apresente outros imóveis. Termine convidando para "
    "agendar uma visita a ESTE imóvel. Escreva valores como 'R$ 750.000,00', sem LaTeX. "
    "As FOTOS do imóvel aparecem automaticamente logo abaixo da sua mensagem: você "
    "pode convidar o lead a vê-las ('dá uma olhada nas fotos abaixo'), mas não "
    "descreva o conteúdo delas."
)


class DetalheImovelAgent:
    def __init__(
        self,
        llm_provider: ILLMProvider,
        repositorio_imoveis: IPropertyRepository,
        dados_mercado: IMarketDataRepository | None = None,
        mapa_bairros: MapaBairros | None = None,
    ) -> None:
        self._llm = llm_provider
        self._repositorio = repositorio_imoveis
        self._dados_mercado = dados_mercado
        self._mapa = mapa_bairros or MapaBairros()

    def __call__(self, estado: EstadoConversa) -> dict:
        todos = self._repositorio.listar_todos()
        ultima_fala_agente = next(
            (m.get("content", "") for m in reversed(estado.get("historico_mensagens") or [])
             if m.get("role") == "assistant"),
            "",
        )
        mostrados = ids_mostrados(ultima_fala_agente)
        por_id = {im.id: im for im in todos}
        imovel = None
        posicao = numero_da_lista(estado["mensagem_usuario"], len(mostrados)) if mostrados else None
        if posicao:  # "fotos do 2"
            imovel = por_id.get(mostrados[posicao - 1])
        if imovel is None:
            imovel = resolver_imovel_citado(
                estado["mensagem_usuario"], todos, estado.get("imovel_interesse_id"), mostrados
            )
        if imovel is None and not candidatos_citados(estado["mensagem_usuario"], todos):
            # Não disse QUAL ("quero ver as fotos"): se o agente mostrou só um,
            # é ele; se mostrou vários, pergunta qual (em vez de escolher sozinho).
            if len(mostrados) > 1:
                opcoes = "\n".join(
                    f"{i}) {por_id[i_d].tipo_imovel} em {por_id[i_d].bairro} — {formatar_moeda(por_id[i_d].preco)}"
                    for i, i_d in enumerate(mostrados, start=1) if i_d in por_id
                )
                return {
                    "imovel_detalhado": True,
                    "resposta_agente": anexar_lista(
                        f"Claro! De qual imóvel você quer ver as fotos? Me diga o número:\n\n{opcoes}", mostrados
                    ),
                }
            alvo = mostrados[0] if mostrados else estado.get("imovel_interesse_id")
            imovel = por_id.get(alvo)
        if imovel is None:
            empatados = candidatos_citados(estado["mensagem_usuario"], todos)
            if len(empatados) > 1:  # ex.: dois imóveis no Tatuapé -> pergunta qual
                opcoes = "\n".join(f"- {im.resumo()}" for im in empatados[:5])
                return {
                    "imovel_detalhado": True,
                    "resposta_agente": anexar_capas(
                        f"Tenho mais de um imóvel com essa descrição. De qual você quer saber mais?\n\n{opcoes}",
                        [im.id for im in empatados[:5] if im.fotos],
                    ),
                }
            # Não deu para saber qual imóvel: o grafo segue para o Consultor.
            return {"imovel_detalhado": False}

        ficha = self.montar_ficha(imovel, estado)
        mensagens = [{"role": "system", "content": _PROMPT}]
        mensagens.extend(historico_anterior(estado))
        mensagens.append(
            {
                "role": "user",
                "content": f"Pergunta do lead: {estado['mensagem_usuario']}\n\nFicha do imóvel:\n{ficha}",
            }
        )
        resposta = self._llm.gerar_resposta(mensagens, temperatura=0.4)
        if imovel.fotos:
            resposta = anexar_fotos(resposta, imovel.id)
        return {
            "imovel_detalhado": True,
            "imovel_interesse_id": imovel.id,
            "imoveis_sugeridos": [imovel],
            "resposta_agente": resposta,
        }

    def montar_ficha(self, imovel: Imovel, estado: EstadoConversa) -> str:
        indicadores = self._dados_mercado.obter_indicadores() if self._dados_mercado else INDICADORES_PADRAO
        negocio = "à venda" if imovel.tipo_negocio == "venda" else "para alugar (valor mensal)"
        linhas = [
            f"- Imóvel: {imovel.titulo} (código {imovel.id}), {negocio}",
            f"- Localização: {imovel.bairro}, {imovel.zona}",
            f"- {imovel.quartos} quarto(s), {imovel.metragem:.0f} m²",
            f"- Preço: {formatar_moeda(imovel.preco)}",
            f"- Descrição: {imovel.descricao}",
        ]
        if imovel.fotos:
            linhas.append(f"- Fotos: {len(imovel.fotos)} imagens (exibidas abaixo da mensagem)")
        vizinhos = self._mapa.vizinhos_de(imovel.bairro)
        if vizinhos:
            linhas.append(f"- Bairros vizinhos: {', '.join(vizinhos)}")

        if imovel.tipo_negocio == "venda" and imovel.metragem:
            preco_m2 = imovel.preco / imovel.metragem
            dados_bairro = indicadores.dados_bairro(imovel.bairro)
            if dados_bairro and dados_bairro.venda_m2:
                ref, onde = dados_bairro.venda_m2, f"média do bairro {imovel.bairro}"
            else:
                ref, onde = indicadores.venda_m2_cidade, f"média de {indicadores.cidade or 'São Paulo'}"
            if ref:
                diferenca = preco_m2 / ref - 1
                posicao = "abaixo" if diferenca < 0 else "acima"
                linhas.append(
                    f"- Preço por m²: {formatar_moeda(preco_m2)} — {formatar_percentual(abs(diferenca))} {posicao} "
                    f"da {onde} ({formatar_moeda(ref)}/m², Índice FipeZAP "
                    f"{indicadores.referencia_fipezap or indicadores.data_referencia})"
                )

        teto = estado.get("faixa_preco_max") or estado.get("ticket_investimento")
        if teto:
            diferenca = imovel.preco / teto - 1
            if diferenca > 0:
                linhas.append(
                    f"- Orçamento do lead: {formatar_moeda(teto)} — o imóvel está "
                    f"{formatar_moeda(imovel.preco - teto)} ({formatar_percentual(diferenca)}) ACIMA"
                )
            else:
                linhas.append(f"- Orçamento do lead: {formatar_moeda(teto)} — o imóvel cabe no orçamento")

        if estado.get("intencao") == "investimento" or imovel.finalidade_investimento:
            analise = analisar_investimento(
                imovel.preco, estado.get("expectativa_retorno"), imovel.metragem, imovel.bairro, indicadores
            )
            linhas.append(f"- Análise de investimento: {analise.descrever()}")
        return "\n".join(linhas)

