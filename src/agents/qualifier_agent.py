"""Agente Qualificador.

Responsabilidade única (SRP): entender a mensagem do lead, extrair dados
(intenção, orçamento, região, urgência...) e decidir o quão "quente" o
lead está. Não sabe nada sobre buscar imóveis, agendar reuniões ou gerar
resumos — isso é responsabilidade de outros agentes.

Recebe `ILLMProvider` por injeção de dependência (constructor injection):
o agente não sabe (nem precisa saber) se por trás tem o Azure OpenAI ou o
MockLLMProvider.
"""
from __future__ import annotations

import re

from src.agents.contexto_conversa import (
    PERGUNTA_CONTINUAR,
    PERGUNTA_VIZINHOS,
    descrever_interesse,
    LIMITE_MENSAGENS_EXTRACAO,
    historico_anterior,
    MARCADOR_HISTORICO,
    MARCADOR_ULTIMA_MENSAGEM,
    formatar_historico,
    normalizar_valor,
)
from src.agents.foco_imovel import ids_mostrados, numero_da_lista, pediu_detalhes
from src.agents.intencao_agendamento import (
    agente_convidou_para_agendar,
    eh_aceite,
    eh_assunto_novo,
    eh_negacao,
    eh_pedido_de_mudanca,
    ha_pergunta_de_horario_aberta,
    extrair_horario,
)
from src.agents.state import EstadoConversa
from src.domain.interfaces import ILLMProvider

_SCHEMA_EXTRACAO = (
    "{intencao: 'compra'|'aluguel'|'investimento'|null, "
    "faixa_preco_min: number|null, faixa_preco_max: number|null, "
    "quartos_desejados: number|null, regiao_interesse: string|null, "
    "urgencia: string|null, ticket_investimento: number|null, "
    "expectativa_retorno: string|null}. "
    "Orçamento: 'até X', 'no máximo X' ou só 'X' -> faixa_preco_max = X e "
    "faixa_preco_min = null; use faixa_preco_min SOMENTE se o lead disser "
    "'a partir de X', 'no mínimo X' ou 'entre X e Y'."
)

# Pedidos do lead para relembrar a conversa ("o que conversamos?").
_PADRAO_PEDIDO_RESUMO = re.compile(
    r"(?<!\w)o que (a gente |nós |nos )?(j[aá] )?(convers|fal|combin)|"
    r"\bresum|\brelembr|\bme lembr|\blembra (do|o) que|\bonde (a gente )?paramos|\bhist[oó]rico"
)
# "quero CONTINUAR com a locação que já conversamos" é pedido para seguir,
# não para relembrar — nesses casos não desviamos para o resumo.
_PADRAO_CONTINUAR = re.compile(r"\bcontinu|\bseguir\b|\bprosseguir")

_NUMEROS_POR_EXTENSO = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3,
    "quatro": 4, "cinco": 5, "seis": 6,
}
# Resposta curta só com um número: "3", "3.", "três", "uns 3", "3 mesmo"
_PADRAO_RESPOSTA_NUMERICA = re.compile(
    r"^\W*(?:uns |umas |acho que |s[oó] )?(\d{1,2}|" + "|".join(_NUMEROS_POR_EXTENSO) + r")\b(?:\s*(?:mesmo|por favor|pf))?\W*$"
)

_PALAVRAS_AGENDAMENTO = {"agendar", "marcar", "reunião", "reuniao", "visita", "visitar"}



# Intenção dita EXPLICITAMENTE na mensagem atual (usada para detectar troca
# de assunto: ex. o lead falava de investimento e agora diz "quero comprar").
_PADROES_INTENCAO = {
    "compra": re.compile(r"\bcompr"),
    "aluguel": re.compile(r"\balug|\bloca[cç][aã]o|\blocar\b"),
    "investimento": re.compile(r"\binvest"),
}

_CAMPOS_PERFIL = (
    "faixa_preco_min",
    "faixa_preco_max",
    "quartos_desejados",
    "regiao_interesse",
    "urgencia",
    "ticket_investimento",
    "expectativa_retorno",
)


def interpretar_valor_monetario(texto: str) -> float | None:
    """'2500' -> 2500; 'até 3 mil' -> 3000; 'R$ 2.500,00' -> 2500;
    '1,2 milhão' -> 1200000; '350k' -> 350000. None se não houver valor."""
    t = texto.lower().replace("r$", " ")
    m = re.search(r"(\d+(?:[.,]\d+)*)\s*(milh[aãõo]\w*|mi\b|mil\b|k\b)?", t)
    if not m:
        return None
    numero, escala = m.group(1), m.group(2) or ""
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", numero):  # 2.500 / 1.200.000,00
        numero = numero.replace(".", "").replace(",", ".")
    else:
        numero = numero.replace(",", ".")
    try:
        valor = float(numero)
    except ValueError:
        return None
    if escala.startswith("milh") or escala == "mi":
        valor *= 1_000_000
    elif escala in ("mil", "k"):
        valor *= 1_000
    return valor if valor >= 100 else None


def classificar_urgencia(texto: str) -> str:
    """'quero me mudar logo' -> 'imediata'; 'em 2 meses' -> '1-3 meses'..."""
    t = texto.lower()
    if re.search(r"sem pressa|n[aã]o tenho pressa|tranquil|s[oó] pesquisando|ano que vem|\bano\b|anos", t):
        return "sem pressa"
    if re.search(r"urgent|imediat|\bj[aá]\b|logo|o quanto antes|agora|este m[eê]s|esse m[eê]s|\b(1|um) m[eê]s|30 dias|semana", t):
        return "imediata"
    if re.search(r"\b([2-3]|dois|duas|tr[eê]s) m[eê]s|trimestre|60 dias|90 dias", t):
        return "1-3 meses"
    if re.search(r"\b([4-6]|quatro|cinco|seis) m[eê]s|semestre", t):
        return "3-6 meses"
    return texto.strip()[:60] or "não informada"


class QualificadorAgent:
    def __init__(self, llm_provider: ILLMProvider) -> None:
        self._llm = llm_provider

    def __call__(self, estado: EstadoConversa) -> dict:
        mensagem = estado["mensagem_usuario"]
        nova_busca = self._detectar_troca_de_intencao(estado)
        recomecar = not nova_busca and self._quer_recomecar(estado)
        if recomecar:
            # "Não" a "quer continuar de onde paramos ou buscar outra coisa?":
            # o lead quer OUTRA coisa. Antes o agente repetia a mesma pergunta
            # em loop. Agora a busca recomeça (a anterior fica registrada).
            anterior = descrever_interesse(dict(estado))
            return {
                **{campo: None for campo in _CAMPOS_PERFIL},
                "intencao": "indefinida",
                "nova_busca": True,
                "recomecar_busca": True,
                "busca_anterior": anterior or None,
                "imovel_interesse_id": None,
                "quer_agendar": False,
                "pediu_resumo": False,
                "pediu_detalhes": False,
                "dados_completos": False,
                "temperatura": "morno",
            }

        if nova_busca:
            # O lead mudou de assunto (ex.: investimento -> compra). O perfil
            # antigo NÃO vale para a nova busca: extraímos só da mensagem atual
            # e deixamos o Esclarecedor perguntar o que faltar. Sem isso, o
            # histórico antigo (região, quartos, ticket...) "contaminava" a nova
            # busca e o agente pulava as perguntas.
            extraido = self._llm.extrair_dados_estruturados(mensagem, _SCHEMA_EXTRACAO)
            base_anterior: dict = {}
        else:
            extraido = self._llm.extrair_dados_estruturados(
                self._montar_texto_extracao(estado), _SCHEMA_EXTRACAO
            )
            base_anterior = estado
        extraido = {campo: normalizar_valor(campo, valor) for campo, valor in extraido.items()}
        extraido.update(self._interpretar_resposta_curta(estado, extraido))
        extraido.update(self._interpretar_urgencia(estado, extraido))
        extraido.update(self._interpretar_valor(estado, extraido))

        atualizacoes: dict = {}
        regiao_antiga = base_anterior.get("regiao_interesse")
        regiao_nova = extraido.get("regiao_interesse")
        if pediu_detalhes(mensagem) and regiao_antiga:
            # "Mais informação do imóvel do Tatuapé" é pergunta sobre UM imóvel,
            # não mudança da região de busca do lead.
            extraido["regiao_interesse"] = regiao_antiga
            regiao_nova = regiao_antiga
        if (
            isinstance(regiao_antiga, str) and isinstance(regiao_nova, str)
            and regiao_nova.strip().lower() in regiao_antiga.lower()
        ):
            # "Zona Leste" depois de "Zona Leste, Mooca": mantém o mais específico.
            extraido["regiao_interesse"] = regiao_antiga

        for campo in _CAMPOS_PERFIL:
            valor_novo = extraido.get(campo)
            if valor_novo is not None:
                atualizacoes[campo] = valor_novo
            elif base_anterior.get(campo) is not None:
                atualizacoes[campo] = base_anterior.get(campo)
            elif nova_busca:
                atualizacoes[campo] = None  # limpa o valor da busca anterior

        intencao_nova = nova_busca or extraido.get("intencao")
        if intencao_nova not in ("compra", "aluguel", "investimento"):
            intencao_nova = None
        if (
            not nova_busca
            and estado.get("intencao") in ("compra", "aluguel", "investimento")
            and intencao_nova != estado.get("intencao")
            and self._intencao_explicita(mensagem) is None
        ):
            # O LLM "achou" outra intenção olhando o histórico (ex.: "Ok" depois
            # de "quero alugar também" voltava para "compra"). Sem o lead dizer
            # explicitamente, a intenção atual é mantida.
            intencao_nova = estado.get("intencao")
        atualizacoes["intencao"] = intencao_nova or estado.get("intencao") or "indefinida"
        atualizacoes["nova_busca"] = bool(nova_busca)
        if nova_busca:
            anterior = descrever_interesse(dict(estado))
            if anterior:
                atualizacoes["busca_anterior"] = anterior

        # Um valor monetário extraído NESTA mensagem só pode significar uma
        # coisa: "faixa de preço" (compra/aluguel) OU "ticket de
        # investimento" — nunca as duas. A extração enxerga só a mensagem
        # atual isolada (sem memória de turnos anteriores — veja o
        # comentário em MockLLMProvider.extrair_dados_estruturados), então
        # se a mensagem não repetir a palavra "investir" (erro de digitação,
        # ou porque a intenção já era conhecida de um turno anterior), o
        # valor pode acabar classificado como faixa de preço por engano.
        # Como a intenção final (`atualizacoes["intencao"]`) já está
        # decidida aqui, corrigimos a classificação com base nela.
        if atualizacoes["intencao"] == "investimento" and extraido.get("faixa_preco_max") is not None:
            atualizacoes["ticket_investimento"] = extraido["faixa_preco_max"]
            atualizacoes.pop("faixa_preco_min", None)
            atualizacoes.pop("faixa_preco_max", None)

        self._normalizar_orcamento(atualizacoes)

        texto = mensagem.lower()
        atualizacoes["pediu_resumo"] = bool(_PADRAO_PEDIDO_RESUMO.search(texto)) and not _PADRAO_CONTINUAR.search(texto)

        atualizacoes["ampliar_busca"] = self._quer_ampliar_busca(estado)
        atualizacoes["quer_agendar"] = False if atualizacoes["ampliar_busca"] else self._detectar_quer_agendar(estado)
        atualizacoes["pediu_detalhes"] = pediu_detalhes(mensagem) or self._escolheu_da_lista(estado)

        atualizacoes["dados_completos"] = self._dados_completos(atualizacoes)
        atualizacoes["temperatura"] = self._calcular_temperatura(atualizacoes)

        return atualizacoes

    @staticmethod
    def _detectar_quer_agendar(estado: EstadoConversa) -> bool:
        """Decide (em código, de forma auditável) se o lead quer agendar.

        1. Negação explícita ("não quero visitar agora") -> False.
        2. Palavra de agendamento na mensagem ("quero marcar uma visita") -> True.
        3. O agente acabou de CONVIDAR para agendar e o lead respondeu com um
           aceite ("sim", "quero", "pode ser") ou com um dia/horário
           ("sábado de manhã", "amanhã às 10h") -> True.
        """
        texto = estado["mensagem_usuario"].strip().lower()
        if eh_negacao(texto):
            return False
        if any(palavra in texto for palavra in _PALAVRAS_AGENDAMENTO):
            return True
        if pediu_detalhes(texto):
            return False  # "quero mais fotos" é pedido de detalhes, não "sim" ao convite
        ultima_fala_agente = next(
            (m["content"] for m in reversed(historico_anterior(estado)) if m["role"] == "assistant"),
            "",
        )
        if not agente_convidou_para_agendar(ultima_fala_agente):
            return False
        # O agente propôs um horário e está esperando resposta: qualquer
        # resposta que não seja um assunto novo (ex.: "quero fia 8 desse mes",
        # com erro de digitação) vai para o Agendador, que entende ou pergunta
        # de novo — em vez de cair nas sugestões de imóveis.
        if ha_pergunta_de_horario_aberta(ultima_fala_agente) and not eh_assunto_novo(texto):
            return True
        return eh_aceite(texto) or eh_pedido_de_mudanca(texto) or extrair_horario(texto) is not None

    @staticmethod
    def _interpretar_valor(estado: EstadoConversa, extraido: dict) -> dict:
        """Se a pergunta pendente era o ORÇAMENTO (ou o valor a investir) e o
        lead respondeu só um valor ("2500", "até 3 mil", "1,2 milhão"), o
        valor é registrado em código — sem depender do LLM."""
        from src.agents.clarifier_agent import EsclarecedorAgent

        campo = EsclarecedorAgent._determinar_campo_faltante(estado).split(" ")[0]
        valor = interpretar_valor_monetario(estado["mensagem_usuario"])
        if valor is None:
            return {}
        if campo == "faixa_preco" and not (extraido.get("faixa_preco_max") or extraido.get("faixa_preco_min")):
            return {"faixa_preco_max": valor}
        if campo == "ticket_investimento" and not extraido.get("ticket_investimento"):
            return {"ticket_investimento": valor}
        return {}

    @staticmethod
    def _interpretar_urgencia(estado: EstadoConversa, extraido: dict) -> dict:
        """Se a pergunta pendente era a URGÊNCIA (decidido em código pelo
        Esclarecedor), a resposta do lead é a urgência — classificada aqui
        para não depender do LLM e não repetir a pergunta em loop."""
        from src.agents.clarifier_agent import EsclarecedorAgent

        if extraido.get("urgencia") or estado.get("urgencia"):
            return {}
        if not EsclarecedorAgent._determinar_campo_faltante(estado).startswith("urgencia"):
            return {}
        return {"urgencia": classificar_urgencia(estado["mensagem_usuario"])}

    @staticmethod
    def _interpretar_resposta_curta(estado: EstadoConversa, extraido: dict) -> dict:
        """Rede de segurança determinística para respostas curtas.

        Se o agente acabou de perguntar "quantos quartos?" e o lead respondeu
        só "3" (ou "três"), o número É a quantidade de quartos — mesmo que a
        extração não tenha entendido isso sozinha (o MockLLMProvider só
        reconhece "3 quartos"). Sem isso, o agente repetia a mesma pergunta
        em loop.
        """
        match = _PADRAO_RESPOSTA_NUMERICA.match(estado["mensagem_usuario"].strip().lower())
        if not match or QualificadorAgent._ids_da_ultima_lista(estado):
            return {}
        bruto = match.group(1)
        numero = int(bruto) if bruto.isdigit() else _NUMEROS_POR_EXTENSO[bruto]

        ultima_pergunta = next(
            (m["content"].lower() for m in reversed(historico_anterior(estado)) if m["role"] == "assistant"),
            "",
        )
        if "quarto" in ultima_pergunta and extraido.get("quartos_desejados") is None:
            return {"quartos_desejados": numero}
        if "retorno" in ultima_pergunta and extraido.get("expectativa_retorno") is None:
            return {"expectativa_retorno": f"{numero}% ao mês"}
        return {}

    @staticmethod
    def _montar_texto_extracao(estado: EstadoConversa) -> str:
        """Envia para a extração a última mensagem JUNTO com o histórico recente.

        Sem o histórico, respostas curtas perdem o sentido: se o agente
        perguntou "quantos quartos?" e o lead respondeu só "3", a LLM não
        tem como saber que "3" são quartos. O histórico também permite
        reconstruir o que o lead disse em conversas anteriores recuperadas.
        """
        mensagem = estado["mensagem_usuario"]
        estado = QualificadorAgent._recortar_busca_atual(estado)
        historico = formatar_historico(estado, limite=LIMITE_MENSAGENS_EXTRACAO)
        if not historico:
            return mensagem
        return f"{MARCADOR_HISTORICO}\n{historico}\n\n{MARCADOR_ULTIMA_MENSAGEM} {mensagem}"

    @staticmethod
    def _normalizar_orcamento(dados: dict) -> None:
        """"Até 350 mil" às vezes vinha do LLM como mínimo E máximo = 350 mil,
        o que excluía da busca qualquer imóvel mais barato (ex.: um de
        R$ 340 mil). Um valor único é sempre o TETO do orçamento."""
        minimo, maximo = dados.get("faixa_preco_min"), dados.get("faixa_preco_max")
        if minimo is not None and maximo is not None:
            if minimo == maximo:
                dados["faixa_preco_min"] = None
            elif minimo > maximo:
                dados["faixa_preco_min"], dados["faixa_preco_max"] = maximo, minimo

    @staticmethod
    def _recortar_busca_atual(estado: EstadoConversa) -> EstadoConversa:
        """Usa só o histórico a partir da última vez que o lead falou de OUTRA
        intenção. Ex.: depois de "agora quero comprar na Mooca", o "500 mil
        para investir" de antes não pode virar orçamento da compra."""
        intencao = estado.get("intencao")
        historico = list(estado.get("historico_mensagens") or [])
        # 1º recorte: se o lead já disse "não, quero outra coisa" à pergunta de
        # continuar, nada antes disso vale para a busca atual.
        for i in range(len(historico) - 1, 0, -1):
            anterior, atual = historico[i - 1], historico[i]
            if (
                atual.get("role") == "user"
                and anterior.get("role") == "assistant"
                and PERGUNTA_CONTINUAR in str(anterior.get("content", ""))
                and QualificadorAgent._resposta_de_recomeco(str(atual.get("content", "")))
            ):
                historico = historico[i:]
                estado = {**estado, "historico_mensagens": historico}
                break
        if intencao not in _PADROES_INTENCAO or not historico:
            return estado
        explicitas: list[tuple[int, str]] = []
        for i, m in enumerate(historico):
            if m.get("role") != "user":
                continue
            texto = str(m.get("content", "")).lower()
            citadas = [n for n, p in _PADROES_INTENCAO.items() if p.search(texto)]
            if len(citadas) == 1:
                explicitas.append((i, citadas[0]))
        ultima_outra = max((i for i, n in explicitas if n != intencao), default=None)
        if ultima_outra is None:
            return estado
        # Começa na 1ª fala que pediu a intenção ATUAL depois da outra (ex.:
        # "quero alugar também"): o que veio entre "quero comprar" e ela
        # (Jardins, 3 quartos, 1 milhão...) é da busca anterior.
        inicio = next((i for i, n in explicitas if i > ultima_outra and n == intencao), ultima_outra + 1)
        return {**estado, "historico_mensagens": historico[inicio:]}

    @staticmethod
    def _escolheu_da_lista(estado: EstadoConversa) -> bool:
        """Depois de uma lista numerada, "2" ou "o segundo" é a escolha de um
        imóvel (e não "2 quartos")."""
        mostrados = QualificadorAgent._ids_da_ultima_lista(estado)
        texto = estado["mensagem_usuario"].strip()
        return bool(mostrados) and len(texto.split()) <= 4 and numero_da_lista(texto, len(mostrados)) is not None

    @staticmethod
    def _ids_da_ultima_lista(estado: EstadoConversa) -> list[str]:
        historico = list(estado.get("historico_mensagens") or [])
        if historico and historico[-1].get("role") == "user":
            historico = historico[:-1]
        ultima = next((m.get("content", "") for m in reversed(historico) if m.get("role") == "assistant"), "")
        return ids_mostrados(ultima)

    @staticmethod
    def _quer_ampliar_busca(estado: EstadoConversa) -> bool:
        """"Sim" à pergunta dos bairros vizinhos, ou pedido explícito ("outros
        bairros", "bairros próximos", "não gostei dessas")."""
        texto = estado["mensagem_usuario"].strip().lower()
        if any(palavra in texto for palavra in _PALAVRAS_AGENDAMENTO):
            return False  # "quero agendar uma visita" é agendamento, não ampliar a busca
        if re.search(
            r"bairros? (pr[oó]xim|vizinh)|outros bairros|outra regi[aã]o|perto dali|arredores|"
            r"mais op[cç][oõ]es|outras op[cç][oõ]es|n[aã]o gostei|n[aã]o me interess|n[aã]o curti",
            texto,
        ):
            return True
        ultima_fala_agente = next(
            (m["content"] for m in reversed(historico_anterior(estado)) if m["role"] == "assistant"), ""
        )
        return PERGUNTA_VIZINHOS in ultima_fala_agente and eh_aceite(texto)

    @staticmethod
    def _quer_recomecar(estado: EstadoConversa) -> bool:
        ultima_fala_agente = next(
            (m["content"] for m in reversed(historico_anterior(estado)) if m["role"] == "assistant"), ""
        )
        if PERGUNTA_CONTINUAR not in ultima_fala_agente:
            return False
        texto = estado["mensagem_usuario"].strip().lower()
        if QualificadorAgent._intencao_explicita(texto):
            return False  # "não, quero alugar" -> troca de intenção normal
        return QualificadorAgent._resposta_de_recomeco(texto)

    @staticmethod
    def _resposta_de_recomeco(texto: str) -> bool:
        texto = texto.strip().lower()
        return bool(
            re.match(r"^\W*n[aã]o\b", texto)
            or re.search(r"outra coisa|outro im[oó]vel|nova busca|come[cç]ar de novo|do zero|diferente", texto)
        )

    @staticmethod
    def _intencao_explicita(texto: str) -> str | None:
        citadas = [nome for nome, padrao in _PADROES_INTENCAO.items() if padrao.search(texto.lower())]
        return citadas[0] if len(citadas) == 1 else None

    @staticmethod
    def _detectar_troca_de_intencao(estado: EstadoConversa) -> str | None:
        """Retorna a nova intenção se o lead, que já tinha uma intenção
        conhecida, pediu EXPLICITAMENTE outra nesta mensagem."""
        anterior = estado.get("intencao")
        if anterior not in ("compra", "aluguel", "investimento"):
            return None
        texto = estado["mensagem_usuario"].lower()
        citadas = [nome for nome, padrao in _PADROES_INTENCAO.items() if padrao.search(texto)]
        if len(citadas) == 1 and citadas[0] != anterior:
            return citadas[0]
        return None

    @staticmethod
    def _dados_completos(dados: dict) -> bool:
        """Só sugerimos imóveis depois de entender o essencial — para compra
        e aluguel: região, quartos E orçamento (faixa de preço)."""
        if dados.get("intencao") == "investimento":
            return bool(dados.get("ticket_investimento")) and bool(dados.get("expectativa_retorno"))
        return (
            dados.get("intencao") in ("compra", "aluguel")
            and bool(dados.get("regiao_interesse"))
            and dados.get("quartos_desejados") is not None
            and bool(dados.get("faixa_preco_min") or dados.get("faixa_preco_max"))
            and bool(dados.get("urgencia"))
        )

    @staticmethod
    def _calcular_temperatura(dados: dict) -> str:
        pontos = 0
        if dados.get("intencao") not in (None, "indefinida"):
            pontos += 1
        if dados.get("dados_completos"):
            pontos += 1
        if dados.get("urgencia") == "imediata":
            pontos += 1
        if dados.get("quer_agendar"):
            pontos += 2

        if pontos >= 3:
            return "quente"
        if pontos >= 1:
            return "morno"
        return "frio"
