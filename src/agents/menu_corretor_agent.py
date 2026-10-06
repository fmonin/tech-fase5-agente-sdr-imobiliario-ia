"""Menu do corretor no chat da "Área do Corretor".

Ao entrar, o corretor vê "O que deseja fazer?" com a lista do que já está
disponível para ele, e pode escolher pelo número ou escrevendo
("registrar visita", "meu desempenho"...). Também conduz, passo a passo,
o registro do resultado de uma visita (qual visita → resultado → motivo).

Determinístico (sem LLM) — só a opção "Ver minha agenda" usa o
`ConsultaAgendaAgent` (que já redige a agenda com o LLM a partir dos dados
reais). Cancelar/remarcar continuam com o `GestaoAgendaCorretorAgent`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from src.domain.entities import Corretor
from src.domain.feedback_visita import MOTIVOS_PERDA, RESULTADOS_VISITA
from src.services.captacao_service import RESULTADOS_CAPTACAO
from src.domain.investimento import formatar_moeda

OPCOES = [
    ("agenda", "📅 Ver minha agenda", r"\b(minha agenda|ver agenda|agenda)\b"),
    ("registrar", "📝 Registrar resultado de visita / negociação (proposta, fechado)",
     r"\b(registr\w*|resultado|pos[- ]?visita|visita realizada|fech\w*|vendi|aluguei|proposta\w*|negocia\w*)\b"),
    ("sem_visita", "🔎 Clientes da minha área sem visita", r"\b(sem visita|sem agendamento|nao agendaram|clientes da (minha )?area)\b"),
    ("sugestoes", "💡 Imóveis para sugerir aos clientes agendados", r"\b(sugest\w*|sugerir|oferecer)\b"),
    ("match", "🏠 Imóveis novos × clientes interessados", r"\b(match|imoveis? nov\w*|cadastr\w*)\b"),
    ("desempenho", "📊 Meu desempenho (funil de vendas)", r"\b(desempenho|funil|metricas?|indicadores|resultados do mes|conversao)\b"),
    ("alterar", "✏️ Remarcar ou cancelar um agendamento", r"\b(remarc\w*|cancel\w*|alterar agendamento)\b"),
    ("captacao", "🏷️ Captação de imóveis (avaliações de imóveis de clientes)", r"\b(capta\w*|avaliac\w*|avaliar imove\w*)\b"),
    ("imoveis_area", "🏘️ Imóveis da minha área (todos os cadastrados)",
     r"\b(imoveis\b.{0,30}\b(minha|da|na) (area|regiao|zona)|meus imoveis|carteira de imoveis|listar imoveis|lista de imoveis)\b"),
]
_PEDIU_MENU = re.compile(r"^\s*(menu|opcoes|opcao|ajuda|help|inicio|voltar|o que (eu )?posso fazer\??|o que (da|e possivel) fazer\??)\s*$")
_ORDINAIS = {"primeir": 1, "segund": 2, "terceir": 3, "quart": 4, "quint": 5, "sext": 6, "setim": 7}


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def texto_menu(nome: Optional[str] = None) -> str:
    linhas = [f"O que deseja fazer{', ' + nome.split()[0] if nome else ''}? Escolha um número ou me diga com suas palavras:"]
    linhas += [f"{i}) {rotulo}" for i, (_, rotulo, _) in enumerate(OPCOES, start=1)]
    return "\n".join(linhas)


def _numero(texto: str, maximo: int) -> Optional[int]:
    t = _normalizar(texto).strip()
    m = re.fullmatch(r"\D{0,12}?(\d{1,2})\D{0,3}", t)
    if m and 1 <= int(m.group(1)) <= maximo:
        return int(m.group(1))
    for raiz, n in _ORDINAIS.items():
        if re.search(rf"\b{raiz}[oa]\b", t) and n <= maximo:
            return n
    return None


MENU_ABERTO = {"fluxo": "menu"}


@dataclass
class RespostaMenu:
    texto: str
    # Por padrão o menu fica "aberto": um número escolhe uma opção.
    pendente: Optional[dict] = None

    def __post_init__(self) -> None:
        if self.pendente is None:
            self.pendente = dict(MENU_ABERTO)


class MenuCorretorAgent:
    def __init__(self, agenda_query_agent, carteira, pos_visita, match, desempenho, captacao=None) -> None:
        self._captacao = captacao
        self._agenda_query = agenda_query_agent
        self._carteira = carteira
        self._pos_visita = pos_visita
        self._match = match
        self._desempenho = desempenho

    def processar(self, corretor: Corretor, mensagem: str, pendente: Optional[dict] = None) -> Optional[RespostaMenu]:
        """Resposta do menu, ou None se a mensagem não é com o menu (aí o
        chat segue para o agente de gestão de agenda)."""
        t = _normalizar(mensagem)
        if _PEDIU_MENU.match(t):
            return RespostaMenu(texto_menu(corretor.nome))
        if pendente and pendente.get("fluxo") == "registrar":
            resposta = self._continuar_registro(corretor, mensagem, pendente)
            if resposta is not None:
                return resposta
        if pendente and pendente.get("fluxo") == "captacao":
            resposta = self._continuar_captacao(corretor, mensagem, pendente)
            if resposta is not None:
                return resposta
        if pendente and pendente.get("fluxo") in ("menu", "registrar", "captacao"):
            n = _numero(mensagem, len(OPCOES))
            if n:
                return self._executar(OPCOES[n - 1][0], corretor)
        # Captação primeiro: "resultado da captação" não é resultado de visita.
        for chave, _, padrao in sorted(OPCOES, key=lambda o: o[0] not in ("captacao", "imoveis_area")):
            if chave not in ("alterar", "agenda") and re.search(padrao, t):
                return self._executar(chave, corretor, mensagem)
        return None

    # ------------------------------------------------------------ opções
    def _executar(self, chave: str, corretor: Corretor, mensagem_original: str = "") -> RespostaMenu:
        rodape = "\n\nDigite **menu** para ver as opções de novo."
        if chave == "agenda":
            return RespostaMenu(self._agenda_query.responder(corretor, "qual é a minha agenda?") + rodape)
        if chave == "registrar":
            return self._iniciar_registro(corretor)
        if chave == "captacao":
            return self._iniciar_captacao(corretor)
        if chave == "imoveis_area":
            return self._imoveis_da_area(corretor, mensagem_original)
        if chave == "sem_visita":
            clientes = self._carteira.clientes_sem_visita(corretor)
            if not clientes:
                return RespostaMenu("Nenhum cliente da sua área está sem visita agora. 🎉" + rodape)
            linhas = [f"🔎 **{len(clientes)} cliente(s) da sua área sem visita** (mais quentes primeiro):"]
            for i, c in enumerate(clientes[:8], start=1):
                linhas.append(f"{i}) **{c.lead.nome}** ({c.lead.perfil.temperatura.value}) — {c.interesse}. _{c.situacao}_")
            linhas.append("Os detalhes e o resumo de cada um estão na aba **🔎 Clientes**.")
            return RespostaMenu("\n".join(linhas) + rodape)
        if chave == "sugestoes":
            itens = self._carteira.sugestoes_para_agendados(corretor)
            if not itens:
                return RespostaMenu("Você não tem clientes agendados no momento." + rodape)
            linhas = ["💡 **Imóveis para levar às visitas:**"]
            for item in itens[:5]:
                escolhido = item.imovel_escolhido.titulo if item.imovel_escolhido else "sem imóvel definido"
                extras = ", ".join(f"{s.imovel.id} ({s.motivo.split(' · ')[0]})" for s in item.sugestoes) or "nenhum parecido"
                linhas.append(f"- **{item.agendamento.cliente_nome}** escolheu {escolhido} → sugira: {extras}")
            return RespostaMenu("\n".join(linhas) + rodape)
        if chave == "match":
            itens = self._match.imoveis_novos_com_match(corretor)
            if not itens:
                return RespostaMenu(
                    "Nenhum imóvel novo na sua área nos últimos 30 dias. Cadastre um na aba **🏠 Imóveis novos** "
                    "que eu mostro na hora quais clientes procuram algo assim." + rodape)
            linhas = ["🏠 **Imóveis novos × clientes interessados:**"]
            for item in itens[:5]:
                nomes = ", ".join(c.lead.nome + (" ✔️avisado" if c.ja_avisado else "") for c in item.clientes) or "nenhum cliente compatível ainda"
                linhas.append(f"- {item.imovel.id} — {item.imovel.titulo} ({item.imovel.bairro}, {formatar_moeda(item.imovel.preco)}): {nomes}")
            linhas.append("Para avisar um cliente, use o botão na aba **🏠 Imóveis novos**.")
            return RespostaMenu("\n".join(linhas) + rodape)
        if chave == "desempenho":
            return RespostaMenu(self._desempenho.calcular(corretor).resumo_texto()
                                + "\n\nOs gráficos estão na aba **📊 Desempenho**." + rodape)
        return RespostaMenu(
            "Claro! Me diga qual agendamento e o que fazer, por exemplo: \"cancela a visita da Ana\" ou "
            "\"muda a visita do Fernando para sexta às 14h\". Nada muda sem a sua confirmação."
        )

    # ------------------------------------------------------------ imóveis da área
    def _imoveis_da_area(self, corretor: Corretor, mensagem: str = "") -> RespostaMenu:
        t = _normalizar(mensagem)
        negocio = "aluguel" if re.search(r"\b(alug\w*|locac\w*)\b", t) else (
            "venda" if re.search(r"\b(venda|vender|comprar)\b", t) else None)
        bairro = self._carteira.bairros_no_texto(mensagem) if mensagem else None
        imoveis = self._carteira.imoveis_da_area(corretor, negocio, bairro)
        area = ", ".join(corretor.zonas_atuacao)
        filtro = " · ".join(x for x in ((("para " + negocio) if negocio else ""), bairro or "") if x)
        if not imoveis:
            return RespostaMenu(f"Não encontrei imóveis na sua área ({area}){' ' + filtro if filtro else ''}."
                                "\n\nDigite **menu** para ver as opções.")
        venda = sum(1 for im in imoveis if im.tipo_negocio == "venda")
        por_bairro = {}
        for im in imoveis:
            por_bairro[im.bairro] = por_bairro.get(im.bairro, 0) + 1
        linhas = [f"🏘️ **{len(imoveis)} imóvel(is) na sua área** ({area}{' · ' + filtro if filtro else ''}): "
                  f"{venda} à venda, {len(imoveis) - venda} para alugar.",
                  "Por bairro: " + ", ".join(f"{b} ({n})" for b, n in sorted(por_bairro.items(), key=lambda x: -x[1]))]
        for im in imoveis[:12]:
            preco = formatar_moeda(im.preco) + ("/mês" if im.tipo_negocio == "aluguel" else "")
            quartos = f" · {im.quartos} qto(s)" if im.quartos else ""
            linhas.append(f"- {im.id} · {im.tipo_imovel}{quartos} · {im.bairro} · {preco}")
        if len(imoveis) > 12:
            linhas.append(f"… e mais {len(imoveis) - 12}. A lista completa (com filtros) está na aba **🏘️ Imóveis da área**.")
        linhas.append("Dica: filtre escrevendo, por exemplo, \"imóveis da área para alugar na Mooca\".")
        return RespostaMenu("\n".join(linhas) + "\n\nDigite **menu** para ver as opções.")

    # ------------------------------------------------------------ captação de imóveis
    def _iniciar_captacao(self, corretor: Corretor) -> RespostaMenu:
        itens = self._captacao.listar(corretor.id) if self._captacao else []
        if not itens:
            return RespostaMenu(
                "🏷️ Nenhuma captação pendente. Quando um cliente quiser vender ou alugar o imóvel dele, o "
                f"{_agente()} faz o cadastro, estima o valor e agenda a avaliação com você."
                "\n\nDigite **menu** para ver as opções.")
        linhas = ["🏷️ **Suas captações** (imóveis de clientes para avaliar):"]
        for i, item in enumerate(itens[:9], start=1):
            a = item.agendamento
            situacao = f" · _{RESULTADOS_CAPTACAO[a.resultado_visita]}_" if a.resultado_visita else ""
            ficha = (a.detalhes or a.imovel_titulo or "").replace("\n", " — ")
            linhas.append(f"{i}) **{a.cliente_nome or 'Cliente'}** · avaliação {a.quando_formatado()}{situacao}\n   {ficha}")
        linhas.append("\nPara registrar o resultado de uma avaliação, me diga o número.")
        return RespostaMenu("\n".join(linhas), {"fluxo": "captacao", "etapa": "escolher",
                                                  "ids": [i.agendamento.id for i in itens[:9]]})

    def _continuar_captacao(self, corretor: Corretor, mensagem: str, p: dict) -> Optional[RespostaMenu]:
        etapa = p.get("etapa")
        if etapa == "escolher":
            n = _numero(mensagem, len(p["ids"]))
            if not n:
                return None
            opcoes = "\n".join(f"{i}) {r}" for i, r in enumerate(RESULTADOS_CAPTACAO.values(), start=1))
            return RespostaMenu(f"Como foi essa avaliação?\n{opcoes}",
                                {"fluxo": "captacao", "etapa": "resultado", "id": p["ids"][n - 1]})
        if etapa == "resultado":
            chaves = list(RESULTADOS_CAPTACAO)
            n = _numero(mensagem, len(chaves))
            if not n:
                return None
            resultado = chaves[n - 1]
            if resultado == "captado":
                return RespostaMenu("Ótimo! Qual o valor de anúncio? (ex.: \"520 mil\" ou \"3.200\" para aluguel)",
                                    {"fluxo": "captacao", "etapa": "valor", "id": p["id"]})
            if resultado == "nao_captado":
                return RespostaMenu("Qual foi o motivo? (ex.: \"preço acima do mercado\", \"fechou com outra imobiliária\")",
                                    {"fluxo": "captacao", "etapa": "motivo", "id": p["id"]})
            return self._finalizar_captacao(corretor, p["id"], resultado)
        if etapa == "valor":
            from src.agents.qualifier_agent import interpretar_valor_monetario

            valor = interpretar_valor_monetario(mensagem)
            if not valor:
                return None
            return self._finalizar_captacao(corretor, p["id"], "captado", valor=valor)
        if etapa == "motivo":
            return self._finalizar_captacao(corretor, p["id"], "nao_captado", motivo=mensagem)
        return None

    def _finalizar_captacao(self, corretor: Corretor, agendamento_id: str, resultado: str,
                            valor: Optional[float] = None, motivo: Optional[str] = None) -> RespostaMenu:
        _, texto, _, _ = self._captacao.registrar_resultado(agendamento_id, corretor, resultado, valor, motivo)
        return RespostaMenu(f"✅ {texto}\n\nDigite **menu** para ver as opções.")

    # ------------------------------------------------------------ registro (passo a passo)
    def _iniciar_registro(self, corretor: Corretor) -> RespostaMenu:
        visitas = self._pos_visita.visitas_para_registrar(corretor.id)
        if not visitas:
            return RespostaMenu("Não há visitas confirmadas aguardando resultado. 👍\n\nDigite **menu** para ver as opções.")
        linhas = ["📝 De qual visita você quer registrar o resultado (ou atualizar a negociação)?"]
        for i, a in enumerate(visitas[:9], start=1):
            imovel = f" — {a.imovel_titulo}" if a.imovel_titulo else ""
            situacao = f" · _em negociação: {RESULTADOS_VISITA[a.resultado_visita]}_" if a.resultado_visita else ""
            linhas.append(f"{i}) {a.cliente_nome or 'Cliente'} · {a.quando_formatado()}{imovel}{situacao}")
        return RespostaMenu("\n".join(linhas), {"fluxo": "registrar", "etapa": "visita", "ids": [a.id for a in visitas[:9]]})

    def _continuar_registro(self, corretor: Corretor, mensagem: str, p: dict) -> Optional[RespostaMenu]:
        etapa = p.get("etapa")
        if etapa == "visita":
            n = _numero(mensagem, len(p["ids"]))
            if not n:
                return None
            opcoes = "\n".join(f"{i}) {rotulo}" for i, rotulo in enumerate(RESULTADOS_VISITA.values(), start=1))
            return RespostaMenu(f"Como foi essa visita?\n{opcoes}", {"fluxo": "registrar", "etapa": "resultado", "id": p["ids"][n - 1]})
        if etapa == "resultado":
            chaves = list(RESULTADOS_VISITA)
            n = _numero(mensagem, len(chaves))
            if not n:
                t = _normalizar(mensagem)
                n = next((i for i, k in enumerate(chaves, start=1) if re.search(
                    {"gostou": r"\b(gostou|negocia)", "proposta": r"\bproposta", "fechado": r"\b(fech|vend|alug)",
                     "nao_gostou": r"\bnao gostou", "nao_compareceu": r"\b(nao (veio|compareceu|apareceu)|faltou)"}[k], t)), None)
                if not n:
                    return None
            resultado = chaves[n - 1]
            if resultado == "nao_gostou":
                motivos = "\n".join(f"{i}) {m}" for i, m in enumerate(MOTIVOS_PERDA, start=1))
                return RespostaMenu(f"Qual foi o principal motivo?\n{motivos}",
                                    {"fluxo": "registrar", "etapa": "motivo", "id": p["id"]})
            if resultado in ("proposta", "fechado"):
                pergunta = "Qual o valor da proposta?" if resultado == "proposta" else "Qual o valor do negócio fechado?"
                return RespostaMenu(f"{pergunta} (ex.: \"480 mil\" ou \"3.200\" para aluguel — ou \"pular\")",
                                    {"fluxo": "registrar", "etapa": "valor", "id": p["id"], "resultado": resultado})
            return self._finalizar(corretor, p["id"], resultado, None)
        if etapa == "valor":
            from src.agents.qualifier_agent import interpretar_valor_monetario

            valor = interpretar_valor_monetario(mensagem)
            if valor is None and not re.search(r"\b(pular|nao sei|depois|sem valor)\b", _normalizar(mensagem)):
                return None
            return self._finalizar(corretor, p["id"], p["resultado"], None, valor)
        if etapa == "motivo":
            n = _numero(mensagem, len(MOTIVOS_PERDA))
            motivo = MOTIVOS_PERDA[n - 1] if n else next(
                (m for m in MOTIVOS_PERDA if _normalizar(m).split()[0] in _normalizar(mensagem)), None)
            if not motivo:
                return None
            return self._finalizar(corretor, p["id"], "nao_gostou", motivo)
        return None

    def _finalizar(self, corretor: Corretor, agendamento_id: str, resultado: str, motivo: Optional[str],
                   valor: Optional[float] = None) -> RespostaMenu:
        _, texto = self._pos_visita.registrar_resultado(agendamento_id, corretor.id, resultado, motivo, valor=valor)
        return RespostaMenu(f"✅ {texto}\n\nDigite **menu** para ver as opções.")


def _agente() -> str:
    from src.config import settings

    return settings.agente_nome
