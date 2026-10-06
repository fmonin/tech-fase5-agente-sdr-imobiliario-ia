"""Agente Consultor de Imóveis.

Responsabilidade única: dado o perfil já qualificado do lead, buscar
imóveis compatíveis (usando o repositório estruturado + busca semântica
RAG) e redigir uma resposta humanizada com as sugestões.

Depende apenas de interfaces (`IPropertyRepository`, `IVectorSearch`,
`ILLMProvider`) — Dependency Inversion Principle.
"""
from __future__ import annotations

import re

from src.agents.apresentacao_imoveis import PERGUNTA_FOTOS, lista_numerada
from src.agents.contexto_conversa import (
    PERGUNTA_CONTINUAR,
    PERGUNTA_VIZINHOS,
    descrever_interesse,
    formatar_perfil,
    historico_anterior,
)
from src.agents.intencao_agendamento import eh_negacao
from src.agents.state import EstadoConversa
from src.config import settings
from src.domain.entities import Imovel
from src.domain.feedback_visita import aplicar_feedback
from src.domain.interfaces import (
    ILLMProvider,
    IMarketDataRepository,
    IPropertyRepository,
    IVectorSearch,
)
from src.domain.investimento import (
    INDICADORES_PADRAO,
    analisar_investimento,
    formatar_moeda,
    formatar_percentual,
)
from src.domain.localizacao import MapaBairros
from src.domain.midia import anexar_lista

# Mensagens curtas de agradecimento/encerramento ("ok, obrigado", "valeu").
# Sem esta regra, o agente reapresentava os mesmos imóveis a cada "obrigado".
_PADRAO_ENCERRAMENTO = re.compile(
    r"^\W*(ok\W*|t[aá]\W*|beleza\W*|perfeito\W*)?(muito )?(obrigad[oa]|valeu|agrade[cç]o|"
    r"tchau|at[eé] mais|at[eé] logo|era isso|s[oó] isso)\b.{0,30}$"
)

# "ok", "ol", "certo", "entendi"...: só um sinal de que leu — não é um novo pedido.
_PADRAO_CONFIRMACAO_CURTA = re.compile(r"^\W*(ok|okay|ol[aá]?|oi+|certo|beleza|blz|entendi|hum+|ah+|t[aá]|uhum|legal)\W*$")

_MAPA_TIPO_NEGOCIO = {"compra": "venda", "aluguel": "aluguel", "investimento": "venda"}


class ConsultorImoveisAgent:
    def __init__(
        self,
        llm_provider: ILLMProvider,
        repositorio_imoveis: IPropertyRepository,
        busca_semantica: IVectorSearch,
        dados_mercado: IMarketDataRepository | None = None,
        mapa_bairros: MapaBairros | None = None,
    ) -> None:
        self._llm = llm_provider
        self._repositorio = repositorio_imoveis
        self._busca = busca_semantica
        self._dados_mercado = dados_mercado
        self._mapa_bairros = mapa_bairros or MapaBairros()

    def __call__(self, estado: EstadoConversa) -> dict:
        if _PADRAO_ENCERRAMENTO.match(estado.get("mensagem_usuario", "").strip().lower()):
            nome = (estado.get("cliente_nome") or "").split(" ")[0]
            saudacao = f", {nome}" if nome else ""
            return {
                "imoveis_sugeridos": [],
                "resposta_agente": (
                    f"Eu que agradeço{saudacao}! Se surgir qualquer dúvida ou quiser "
                    "ver outras opções, é só me chamar por aqui."
                ),
            }

        if _PADRAO_CONFIRMACAO_CURTA.match(estado.get("mensagem_usuario", "").strip().lower()):
            return {
                "imoveis_sugeridos": [],
                "resposta_agente": (
                    "Certo! Como posso te ajudar agora? Posso ajustar a busca (região, quartos ou "
                    "orçamento), te mostrar as fotos de algum imóvel ou agendar uma visita."
                ),
            }

        # "Não" curto (ex.: recusou o convite para agendar): em vez de
        # reapresentar imóveis, pergunta como o lead quer seguir.
        mensagem = estado.get("mensagem_usuario", "").strip().lower()
        if eh_negacao(mensagem) and len(re.findall(r"\w+", mensagem)) <= 4:
            interesse = descrever_interesse(dict(estado))
            continuar = f"continuar com {interesse}" if interesse else "continuar de onde paramos"
            return {
                "imoveis_sugeridos": [],
                "resposta_agente": (
                    f"Sem problemas! Quer {continuar} {PERGUNTA_CONTINUAR} "
                    "Me conta o que você tem em mente que eu te ajudo. 🙂"
                ),
            }

        imoveis = self._buscar_imoveis(estado)
        # Retorno de visitas anteriores ("achei caro", "pequeno"...): tira o que o
        # cliente recusou e ajusta pelo motivo.
        imoveis, motivos_considerados = aplicar_feedback(imoveis, estado.get("feedback_visitas"))

        if not imoveis:
            resposta = self._resposta_sem_resultado(estado)
        else:
            lista_para_llm = self._montar_lista(estado, imoveis)
            introducao = self._redigir_resposta(estado, lista_para_llm).strip()
            indicadores = self._dados_mercado.obter_indicadores() if self._dados_mercado else INDICADORES_PADRAO
            if motivos_considerados:
                introducao += (" (Já considerei o que você achou da visita: "
                               + ", ".join(m.lower() for m in motivos_considerados) + ".)")
            resposta = (
                f"{introducao}\n\n{lista_numerada(imoveis, estado.get('intencao', ''), indicadores)}"
                f"\n\n{PERGUNTA_FOTOS}"
            )
            if self._oferecer_vizinhos:
                # Primeiro o bairro pedido; os vizinhos só se o lead quiser.
                resposta += (
                    f"\nSe nenhuma dessas te agradar, também tenho opções nos bairros vizinhos "
                    f"({', '.join(self._oferecer_vizinhos)}). {PERGUNTA_VIZINHOS}"
                )
            # Fotos só quando o lead pedir; a ordem da lista fica marcada para "fotos do 2".
            resposta = anexar_lista(resposta, [im.id for im in imoveis])
        foco = {"imovel_interesse_id": imoveis[0].id} if imoveis else {}
        return {"imoveis_sugeridos": imoveis, "resposta_agente": resposta, **foco}

    def _geografia(self, termos: list[str]) -> dict:
        """Bairros/zonas pedidos, bairros vizinhos e a referência para textos."""
        mapa = self._mapa_bairros
        bairros = [mapa.nome_oficial(t) for t in termos if mapa.eh_bairro(t)]
        zonas = [z for z in dict.fromkeys(
            [mapa.zona_de(b) for b in bairros] + [t.strip().title() for t in termos if t.lower().startswith("zona")]
        ) if z]  # "zona Leste" -> "Zona Leste" (antes a diferença de maiúscula quebrava a comparação)
        vizinhos = [v for v in dict.fromkeys(v for b in bairros for v in mapa.vizinhos_de(b)) if v not in bairros]
        if not bairros:  # pediu só a zona: "perto" = bairros na divisa da zona
            vizinhos = list(dict.fromkeys(v for z in zonas for v in mapa.vizinhos_da_zona(z)))
        referencia = bairros[0] if bairros else (termos[0] if termos else "região pedida")
        return {"bairros": bairros, "zonas": zonas, "vizinhos": vizinhos, "referencia": referencia}

    def _onde(self, im: Imovel, geo: dict) -> str:
        if any(im.bairro.lower() == b.lower() for b in geo["bairros"]):
            return f"no bairro pedido ({im.bairro})"
        if any(im.bairro.lower() == v.lower() for v in geo["vizinhos"]):
            if geo["bairros"]:
                return f"em {im.bairro}, bairro VIZINHO de {geo['referencia']}"
            return f"em {im.bairro} ({im.zona}), bairro na divisa da {geo['referencia']}"
        if im.zona.lower() in [z.lower() for z in geo["zonas"]]:
            if not geo["bairros"]:
                return f"em {im.bairro}, na região pedida ({im.zona})"
            return f"em {im.bairro}, na mesma zona ({im.zona}) de {geo['referencia']}"
        return f"em OUTRA região ({im.bairro}/{im.zona}), longe de {geo['referencia']}"

    def _buscar_imoveis(self, estado: EstadoConversa) -> list[Imovel]:
        """Busca como um corretor faria — do mais perto ao mais longe, sem
        nunca ignorar em silêncio o tipo de negócio, o orçamento e os quartos:

        1. No BAIRRO pedido: primeiro o que bate exatamente; depois, no MESMO
           bairro, o que tem valor próximo (até 40% acima) ou 1 quarto a menos
           — e o agente oferece mostrar os bairros vizinhos se não agradar.
        2. Bairros VIZINHOS (quando não há nada no bairro, ou quando o lead
           pede "outros bairros"/"não gostei"): dentro do perfil, depois com
           valores próximos.
        3. Mesma zona.
        4. Outra região só como último recurso (sempre sinalizado).
        5. Busca semântica (RAG), ainda filtrada.
        Investimento tem regra própria: compara a RENTABILIDADE estimada
        (dados de mercado) e sugere onde o retorno é maior.

        Cada imóvel aproximado recebe uma observação (`self._observacoes`) que
        vai para o prompt, para o agente ser transparente com o lead.
        """
        self._observacoes: dict[str, str] = {}
        self._info_regiao: str = ""
        self._oferecer_vizinhos: list[str] = []
        intencao = estado.get("intencao", "indefinida")
        q = estado.get("quartos_desejados") if intencao != "investimento" else None
        teto = estado.get("faixa_preco_max") or estado.get("ticket_investimento")
        filtros = {
            "tipo_negocio": _MAPA_TIPO_NEGOCIO.get(intencao),
            "preco_max": teto,
            "preco_min": estado.get("faixa_preco_min"),
            "quartos_min": q,
        }
        flex = {**filtros, "preco_min": None}
        proximo = {**flex, "preco_max": teto * 1.4 if teto else None, "quartos_min": max(1, q - 1) if q else None}
        termos = self._termos_regiao(estado.get("regiao_interesse"))
        geo = self._geografia(termos)

        if intencao == "investimento":
            return self._buscar_investimento(estado, flex, termos, geo)

        escolhidos: dict[str, Imovel] = {}

        def adicionar(im: Imovel, observacao: str = "") -> None:
            if im.id not in escolhidos and len(escolhidos) < 3:
                escolhidos[im.id] = im
                if observacao:
                    self._observacoes[im.id] = observacao

        def diferencas(im: Imovel) -> str:
            itens = []
            if q and im.quartos < q:
                itens.append(f"tem {im.quartos} quarto(s) (você pediu {q})")
            if teto and im.preco > teto:
                itens.append(f"{formatar_moeda(im.preco)}, {round((im.preco / teto - 1) * 100)}% ACIMA do orçamento")
            return " e ".join(itens) or "dentro do perfil"

        ampliar = bool(estado.get("ampliar_busca")) and bool(geo["vizinhos"] or geo["zonas"])

        def mais_parecidos(imoveis: list[Imovel]) -> list[Imovel]:
            # primeiro os com os quartos pedidos, depois o valor mais perto do orçamento
            return sorted(imoveis, key=lambda im: (bool(q and im.quartos < q), abs(im.preco - teto) if teto else 0))

        # 1. No bairro/região pedida
        if termos and not ampliar:
            for im in self._buscar_por_regiao(filtros, termos):
                adicionar(im)
            for im in mais_parecidos(self._buscar_por_regiao(proximo, termos)):
                adicionar(im, f"{self._onde(im, geo)}, valor/perfil próximo: {diferencas(im)}")
            if escolhidos:
                if geo["bairros"]:  # pediu um bairro: os vizinhos ficam como próxima opção
                    com_opcoes = {im.bairro for im in self._buscar_por_regiao(proximo, geo["vizinhos"])}
                    # vizinhos que TÊM imóveis com esse perfil aparecem primeiro
                    self._oferecer_vizinhos = sorted(geo["vizinhos"], key=lambda b: b not in com_opcoes)[:4]
                return list(escolhidos.values())
        elif not termos:
            for im in self._buscar_por_regiao(filtros, [None]):
                adicionar(im)
            if escolhidos:
                return list(escolhidos.values())

        # 2. Bairros vizinhos e 3. mesma zona — primeiro dentro do perfil, depois próximos
        perto = geo["vizinhos"] + [z for z in geo["zonas"]]
        if perto:
            for im in self._buscar_por_regiao(flex, perto):
                if im.bairro.lower() not in [b.lower() for b in geo["bairros"]]:
                    adicionar(im, f"{self._onde(im, geo)} — {diferencas(im)}")
            for im in mais_parecidos(self._buscar_por_regiao(proximo, perto)):
                if im.bairro.lower() not in [b.lower() for b in geo["bairros"]]:
                    adicionar(im, f"{self._onde(im, geo)}, valor/perfil próximo: {diferencas(im)}")
            if escolhidos:
                return list(escolhidos.values())

        # 4. Outra região (último recurso, sempre sinalizado)
        for im in self._buscar_por_regiao(flex, [None]):
            adicionar(im, f"{self._onde(im, geo)} — {diferencas(im)}")
        if escolhidos:
            if termos:
                self._info_regiao = (
                    f"Não há opções em {geo['referencia']} nem nos bairros próximos com esse perfil; "
                    "deixe claro que as opções abaixo ficam em outra região."
                )
            return list(escolhidos.values())

        # 5. RAG como último recurso — respeitando os filtros essenciais.
        falas_lead = " ".join(
            m["content"] for m in historico_anterior(estado, limite=6) if m.get("role") == "user"
        )
        consulta = " ".join(
            str(parte)
            for parte in (estado.get("intencao"), estado.get("regiao_interesse"),
                          f"{q} quartos" if q else None, falas_lead, estado["mensagem_usuario"])
            if parte
        )
        limite = teto * 1.4 if teto else None
        semelhantes = [
            im
            for im in self._busca.buscar_similares(consulta, top_k=8)
            if (not filtros["tipo_negocio"] or im.tipo_negocio == filtros["tipo_negocio"])
            and (limite is None or im.preco <= limite)
            and (q is None or im.quartos >= max(1, q - 1))
        ][:3]
        for im in semelhantes:
            self._observacoes[im.id] = f"sugestão aproximada — {self._onde(im, geo)}: {diferencas(im)}"
        return semelhantes

    def _buscar_investimento(self, estado: EstadoConversa, flex: dict, termos: list[str], geo: dict) -> list[Imovel]:
        """Investidor: o que importa é o RETORNO. Compara a rentabilidade
        estimada (aluguel de mercado do bairro / preço — `analisar_investimento`)
        dos imóveis que cabem no valor a investir e, se um bairro próximo rende
        mais que o pedido, mostra isso com os números."""
        indicadores = self._dados_mercado.obter_indicadores() if self._dados_mercado else INDICADORES_PADRAO
        teto = flex.get("preco_max")
        busca = {**flex, "tipo_negocio": "venda", "quartos_min": None,
                 "preco_max": teto * 1.05 if teto else None}
        area = termos + geo["vizinhos"] + geo["zonas"] if termos else [None]
        candidatos = self._buscar_por_regiao(busca, area) or self._buscar_por_regiao(busca, [None])
        if teto:  # foco no valor que o investidor quer aplicar (não em imóveis muito mais baratos)
            candidatos = [im for im in candidatos if im.preco >= teto * 0.5] or candidatos

        def rentab(im: Imovel) -> float:
            return analisar_investimento(im.preco, None, im.metragem, im.bairro, indicadores).rentabilidade_aa

        no_bairro = [im for im in candidatos if self._onde(im, geo).startswith("no bairro pedido")]
        if not geo["bairros"] and termos:  # pediu só a zona
            no_bairro = [im for im in candidatos if im.zona.lower() in [z.lower() for z in geo["zonas"]]]
        outros = sorted((im for im in candidatos if im not in no_bairro), key=rentab, reverse=True)
        escolhidos: list[Imovel] = []

        melhor_local = max(no_bairro, key=rentab) if no_bairro else None
        if melhor_local:
            escolhidos.append(melhor_local)
            self._observacoes[melhor_local.id] = (
                f"{self._onde(melhor_local, geo)} — rentabilidade bruta estimada de "
                f"{formatar_percentual(rentab(melhor_local))} ao ano"
            )
        elif termos:
            self._info_regiao = (
                f"Não há imóvel para investir em {geo['referencia']} até {formatar_moeda(teto or 0)} na base; "
                "as opções abaixo ficam próximas."
            )
        for im in outros:
            if len(escolhidos) >= 3:
                break
            if melhor_local and rentab(im) > rentab(melhor_local):
                obs = (
                    f"{self._onde(im, geo)} — rentabilidade bruta estimada de {formatar_percentual(rentab(im))} "
                    f"ao ano, MAIOR que a opção em {melhor_local.bairro} ({formatar_percentual(rentab(melhor_local))})"
                )
            elif melhor_local:
                continue  # rende menos que a opção no bairro pedido: não vale sugerir
            else:
                obs = f"{self._onde(im, geo)} — rentabilidade bruta estimada de {formatar_percentual(rentab(im))} ao ano"
            escolhidos.append(im)
            self._observacoes[im.id] = obs
        for im in sorted(no_bairro, key=rentab, reverse=True):  # completa com outras do bairro
            if len(escolhidos) < 3 and im not in escolhidos:
                escolhidos.append(im)
                self._observacoes[im.id] = (
                    f"{self._onde(im, geo)} — rentabilidade bruta estimada de {formatar_percentual(rentab(im))} ao ano"
                )
        return escolhidos

    @staticmethod
    def _resposta_sem_resultado(estado: EstadoConversa) -> str:
        """Em vez de encerrar ("vou repassar seu perfil"), mostra o que foi
        buscado e oferece caminhos para o lead ajustar a busca."""
        criterios = []
        if estado.get("quartos_desejados"):
            criterios.append(f"{estado['quartos_desejados']} quarto(s)")
        if estado.get("regiao_interesse"):
            criterios.append(f"em {estado['regiao_interesse']}")
        teto = estado.get("faixa_preco_max") or estado.get("ticket_investimento")
        if teto:
            criterios.append(f"até {formatar_moeda(teto)}")
        busca = ", ".join(criterios) or "com esse perfil"
        return (
            f"Hoje não tenho na nossa base um imóvel {busca}. Posso procurar com menos quartos, "
            "em outra região ou com outro orçamento — o que prefere ajustar? Se quiser, também "
            "repasso seu perfil para um corretor buscar opções fora da nossa base."
        )

    def _buscar_por_regiao(self, parametros: dict, zonas: list) -> list[Imovel]:
        parametros = {k: v for k, v in parametros.items() if k != "zonas"}
        vistos: dict[str, Imovel] = {}
        for zona in zonas or [None]:
            for im in self._repositorio.buscar(zona=zona, **parametros):
                vistos.setdefault(im.id, im)
        return sorted(vistos.values(), key=lambda im: im.preco)

    @staticmethod
    def _termos_regiao(regiao: str | None) -> list[str]:
        """'Zona Leste, Mooca' -> ['Zona Leste', 'Mooca'] (qualquer um serve)."""
        if not regiao:
            return []
        return [t.strip() for t in re.split(r",|/|;|\be\b|\bou\b", regiao) if t.strip()]

    def _montar_lista(self, estado: EstadoConversa, imoveis: list[Imovel]) -> str:
        """Lista de imóveis para o prompt. Para investidores, cada imóvel
        já vem com a análise financeira CALCULADA EM PYTHON
        (`src/domain/investimento.py`) — o LLM não faz contas."""
        observacoes = getattr(self, "_observacoes", {})

        def obs(im: Imovel) -> str:
            return f" [observação: {observacoes[im.id]}]" if im.id in observacoes else ""

        rodape = f"\nInformação para o lead: {self._info_regiao}" if getattr(self, "_info_regiao", "") else ""
        if estado.get("intencao") != "investimento":
            return "\n".join(f"- {im.resumo()}{obs(im)}" for im in imoveis) + rodape
        indicadores = (
            self._dados_mercado.obter_indicadores() if self._dados_mercado else INDICADORES_PADRAO
        )
        linhas = []
        for im in imoveis:
            analise = analisar_investimento(
                im.preco,
                estado.get("expectativa_retorno"),
                metragem=im.metragem,
                bairro=im.bairro,
                indicadores=indicadores,
            )
            linhas.append(f"- {im.resumo()}{obs(im)}\n  Análise financeira: {analise.descrever()}")
        return "\n".join(linhas) + rodape

    def _redigir_resposta(self, estado: EstadoConversa, lista_imoveis: str) -> str:
        """O LLM escreve só a INTRODUÇÃO (1-3 frases). A lista com os dados de
        cada imóvel é montada em código (`apresentacao_imoveis`), para o
        formato e os números serem sempre os mesmos."""
        prompt_sistema = (
            f"Você é {settings.agente_nome}, um agente imobiliário (SDR/pré-vendas) humanizado. "
            "O sistema vai mostrar ao lead, logo abaixo da sua mensagem, uma LISTA NUMERADA com os "
            "imóveis (tipo, quartos, vagas, bairro, valores, condomínio e retorno). Escreva SOMENTE uma "
            "introdução curta e natural (1 a 3 frases) para essa lista. Regras:\n"
            "- NÃO repita a lista, NÃO cite preços de cada imóvel e NÃO faça perguntas (o sistema "
            "pergunta depois se o lead quer ver fotos).\n"
            "- Use as observações para ser transparente e coerente: se as opções estão no bairro pedido "
            "mas acima do orçamento ou com menos quartos, diga isso; se estão em bairros vizinhos ou na "
            "mesma zona, diga exatamente isso (um bairro vizinho da MESMA zona não é 'outra região'); "
            "só fale em 'outra região' quando a observação disser OUTRA região.\n"
            "- INVESTIMENTO: destaque em uma frase qual opção tem o MAIOR retorno estimado e, se a "
            "expectativa do lead estiver acima do mercado, diga isso com gentileza (valores brutos, "
            "estimados com dados de mercado). Nunca faça contas nem invente números.\n"
            "- Não cumprimente de novo e não prometa horários de visita."
        )
        mensagens = [{"role": "system", "content": prompt_sistema}]
        mensagens.extend(historico_anterior(estado))
        mensagens.append(
            {
                "role": "user",
                "content": (
                    f"Perfil do lead:\n{formatar_perfil(estado)}\n\n"
                    f"Mensagem do lead: {estado['mensagem_usuario']}\n\n"
                    f"Introdução curta para estes imóveis encontrados (com observações):\n{lista_imoveis}"
                ),
            }
        )
        return self._llm.gerar_resposta(mensagens, temperatura=0.4)

