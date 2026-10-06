"""Regras determinísticas para entender respostas do lead sobre agendamento.

Compartilhado pelo `QualificadorAgent` (decide SE o lead quer agendar) e pelo
`AgendadorAgent` (decide se o lead CONFIRMOU o horário proposto ou pediu
OUTRO dia/horário).

Por que em código e não no LLM: respostas como "sim", "pode ser" ou
"sábado de manhã" só fazem sentido junto com a pergunta anterior do agente.
Quando isso dependia de palavras-chave na mensagem atual, "Sim, eu quero"
não era reconhecido e o agente repetia as sugestões de imóveis em loop.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

# Frase fixa usada pelo AgendadorAgent ao PROPOR um horário. Serve de
# marcador para saber, no turno seguinte, que existe uma proposta pendente.
PERGUNTA_CONFIRMACAO_HORARIO = "Esse horário funciona para você?"

_PADRAO_CONVITE = re.compile(r"agend|visita|reuni[aã]o|marcar|bom momento|hor[aá]rio")
_PADRAO_ACEITE = re.compile(
    r"^\W*(sim|s|quero|claro|pode|podemos|bora|vamos|ok|okay|beleza|blz|"
    r"com certeza|perfeito|[oó]timo|aceito|gostaria|tenho interesse|fechado|"
    r"isso|combinado|show|top|legal|funciona|confirmo|confirmado|por favor)\b"
)
_PADRAO_NEGACAO = re.compile(r"\b(n[aã]o|nunca|depois eu vejo)\b")

_DIAS_SEMANA = {
    "segunda": 0, "terça": 1, "terca": 1, "quarta": 2, "quinta": 3,
    "sexta": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}
_NOMES_DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
               "sexta-feira", "sábado", "domingo"]
_PERIODOS = {"manhã": 10, "manha": 10, "tarde": 15, "noite": 19}


# "quero mudar o horário", "outro dia", "não posso nesse horário", "prefiro..."
_PADRAO_MUDANCA = re.compile(
    r"\b(mud|troc|alter|remarc|reagend)\w*|\boutr[oa] (dia|hor|data|data)\w*|"
    r"\bn[aã]o (posso|consigo|d[aá]|vou poder)\b|\bprefir\w*|\bprefer\w*"
)
# Pergunta feita pelo Agendador quando o lead quer trocar o horário proposto.
PERGUNTA_NOVO_HORARIO = "Para qual dia e horário você prefere?"


# Pedido NOVO (não é resposta à pergunta de agendamento): fala de imóveis,
# fotos, região, compra/aluguel... Usado para não "prender" o lead no
# agendamento quando ele muda de assunto.
_PADRAO_ASSUNTO_NOVO = re.compile(
    r"\b(compr|alug|loca[cç]|invest|im[oó]ve|apartament|casa|studio|cobertura|quarto|"
    r"regi[aã]o|zona|bairro|foto|detalh|op[cç][oõ]es|outro im|pre[cç]o|valor|or[cç]amento)"
)


def eh_assunto_novo(texto: str) -> bool:
    return bool(_PADRAO_ASSUNTO_NOVO.search(texto.lower()))


def ha_pergunta_de_horario_aberta(fala_agente: str) -> bool:
    """O agente acabou de propor/perguntar um horário e espera resposta."""
    return PERGUNTA_CONFIRMACAO_HORARIO in fala_agente or PERGUNTA_NOVO_HORARIO in fala_agente


def eh_pedido_de_mudanca(texto: str) -> bool:
    return bool(_PADRAO_MUDANCA.search(texto.lower()))


def eh_negacao(texto: str) -> bool:
    return bool(_PADRAO_NEGACAO.search(texto.lower()))


# Uma resposta de aceite é CURTA ("sim", "quero sim", "pode confirmar",
# "pode ser este dia"). "Quero comprar um imóvel na Zona Leste" também começa
# com "quero", mas é um PEDIDO NOVO — antes isso era lido como "sim" ao
# convite e o lead caía direto no agendamento, sem nenhuma pergunta.
_MAX_PALAVRAS_ACEITE = 4
_PADRAO_PEDIDO_NOVO = re.compile(
    r"\b(compr|alug|loca[cç]|invest|im[oó]ve|apartament|casa|quarto|regi[aã]o|zona|bairro|r\$|mil\b|\d|"
    r"foto|imagem|imagens|detalh|informa)"
)


def eh_aceite(texto: str) -> bool:
    texto = texto.strip().lower()
    if not _PADRAO_ACEITE.search(texto) or eh_negacao(texto) or eh_pedido_de_mudanca(texto):
        return False
    if _PADRAO_PEDIDO_NOVO.search(texto):
        return False
    return len(re.findall(r"\w+", texto)) <= _MAX_PALAVRAS_ACEITE


def agente_convidou_para_agendar(fala_agente: str) -> bool:
    """O agente terminou com um CONVITE (pergunta) sobre agendamento?

    Exige "?" para não confundir com uma confirmação ("Sua visita está
    confirmada."), o que causaria um novo agendamento a cada "ok, obrigado".
    """
    return "?" in fala_agente and bool(_PADRAO_CONVITE.search(fala_agente.lower()))


_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
# "13h", "13h30", "13:30", "13hs", "13hrs", "13 horas", "às 13", "as 9"
_PADRAO_HORA = re.compile(
    r"\b(\d{1,2})\s*(?:h(?:rs?|s|oras?)?|:)\s*(\d{2})?(?![\d/])|\b[àa]s\s+(\d{1,2})(?:[:h](\d{2}))?\b(?![\d/])"
)


def _data_do_dia(t: str, agora: datetime) -> Optional[tuple[datetime, str]]:
    """'dia 8', 'dia 8 desse mês', '08/10', '8 de outubro', 'dia 5 do mês que vem'."""
    m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?\b", t)
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        ano = int(m.group(3)) if m.group(3) else agora.year
        ano = ano + 2000 if ano < 100 else ano
    else:
        m = re.search(r"\b(?:dia\s+)?(\d{1,2})\s+de\s+(" + "|".join(_MESES) + r")\b", t)
        if m:
            dia, mes, ano = int(m.group(1)), _MESES[m.group(2)], agora.year
        else:
            m = re.search(r"\bdia\s+(\d{1,2})\b", t) or re.search(
                r"\b(\d{1,2})\s+(?:desse|deste|do|neste|nesse)\s+m[eê]s\b", t
            )
            if not m:
                return None
            dia, mes, ano = int(m.group(1)), agora.month, agora.year
            if re.search(r"m[eê]s que vem|pr[oó]ximo m[eê]s", t) or dia < agora.day:
                mes, ano = (1, ano + 1) if mes == 12 else (mes + 1, ano)
    try:
        data = datetime(ano, mes, dia)
    except ValueError:
        return None
    if data.date() < agora.date():  # data já passou neste ano -> próximo ano
        data = data.replace(year=data.year + 1)
    return data, f"dia {data:%d/%m}"


def extrair_horario(texto: str, agora: Optional[datetime] = None) -> Optional[dict]:
    """Interpreta dia/horário em linguagem natural (regras simples).

    Retorna {"texto", "data_hora"} ou None se não houver dia nem horário.
    Exemplos: "amanhã às 15h", "sábado de manhã", "sexta 14:30", "hoje à noite",
    "dia 8 desse mês às 13hrs", "08/10 às 9", "8 de outubro 13 horas".
    """
    agora = agora or datetime.now()
    t = texto.lower()

    data_base: Optional[datetime] = None
    rotulo = None
    if "depois de amanh" in t:
        data_base, rotulo = agora + timedelta(days=2), "depois de amanhã"
    elif re.search(r"amanh[aã]", t):
        data_base, rotulo = agora + timedelta(days=1), "amanhã"
    elif re.search(r"\bhoje\b", t):
        data_base, rotulo = agora, "hoje"
    else:
        dia_mes = _data_do_dia(t, agora)
        if dia_mes:
            data_base, rotulo = dia_mes
        else:
            for nome, indice in _DIAS_SEMANA.items():
                if re.search(rf"\b{nome}", t):
                    data_base = agora + timedelta(days=(indice - agora.weekday()) % 7 or 7)
                    rotulo = _NOMES_DIAS[indice]
                    break
            if data_base is None and re.search(r"semana que vem|pr[oó]xima semana", t):
                data_base, rotulo = agora + timedelta(days=7), "próxima semana"

    hora = minuto = None
    for m in _PADRAO_HORA.finditer(t):
        h = int(m.group(1) or m.group(3))
        if 7 <= h <= 21:
            hora, minuto = h, int(m.group(2) or m.group(4) or 0)
            break
    if hora is None:
        for periodo, h in _PERIODOS.items():
            if re.search(rf"\b{periodo}\b", t):
                hora, minuto = h, 0
                break

    if data_base is None and hora is None:
        return None
    if data_base is None:
        data_base, rotulo = agora + timedelta(days=1), "amanhã"
    if hora is None:
        hora, minuto = 10, 0

    data = data_base.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    if not rotulo.startswith("dia "):
        rotulo = f"{rotulo} ({data:%d/%m})"
    hora_txt = f"{hora}h" if not minuto else f"{hora}h{minuto:02d}"
    return {"texto": f"{rotulo} às {hora_txt}", "data_hora": data}
