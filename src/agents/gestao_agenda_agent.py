"""Agente de Gestão de Agenda do Corretor (chat da "Área do Corretor").

Permite ao corretor CANCELAR ou REMARCAR compromissos em linguagem natural:
    "cancela a reunião de amanhã com o Fernando, tive um imprevisto"
    "muda a visita do Fernando para sexta às 14h"
e continua respondendo consultas ("o que eu tenho hoje?") via
`ConsultaAgendaAgent`.

Divisão de responsabilidades (mesmo princípio do resto do projeto):
    - LLM: só INTERPRETA a frase (ação, qual compromisso, novo horário,
      motivo) e devolve um JSON.
    - Código: valida tudo — o compromisso precisa existir e ser do corretor,
      o novo horário é convertido por `extrair_horario` (determinístico) — e,
      se o LLM não entender (ou estiver no modo Mock), usa regras simples.
    - Nada é alterado sem CONFIRMAÇÃO explícita do corretor ("sim"/"não").
      A ação pendente fica guardada pela interface entre um turno e outro.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.agents.agenda_query_agent import ConsultaAgendaAgent
from src.agents.intencao_agendamento import eh_aceite, eh_negacao, extrair_horario
from src.domain.entities import Agendamento, Corretor
from src.domain.interfaces import ILLMProvider
from src.services.agenda_service import AgendaService

_PADRAO_CANCELAR = re.compile(r"\b(cancel|desmarc|exclu|remov|apag|desist)\w*")
_PADRAO_ALTERAR = re.compile(r"\b(alter|mud|remarc|reagend|troc|transfer|adi[ae]|antecip|passa)\w*")
# "2", "o 2", "número 2", "#2", "nº 2" (referência à lista numerada)
_PADRAO_NUMERO = re.compile(
    r"^\W*(?:o|a|e o|e a|numero|n[o°º])?\s*(\d{1,2})\W*$|(?:#|numero\s*|\bn[o°º]\s*)(\d{1,2})\b"
)
_PADRAO_MOTIVO = re.compile(r"(?:motivo[:\s]+|porque\s+|pois\s+|devido a\s+|,\s*(?=tive\b|vou\b|estou\b))(.+)$")

_PROMPT_INTERPRETACAO = (
    "Você interpreta pedidos de um corretor de imóveis sobre a PRÓPRIA agenda. "
    "Responda SOMENTE com um JSON válido, sem markdown, no formato: "
    '{"acao": "cancelar" | "alterar" | "consultar", "numero": inteiro ou null, '
    '"novo_dia_horario": texto ou null, "motivo": texto ou null}. '
    "'numero' é o número do compromisso na lista fornecida a que o pedido se "
    "refere (null se não der para saber). 'novo_dia_horario' é o novo dia/horário "
    "pedido, copiado das palavras do corretor (ex.: 'sexta às 14h'), só para "
    "'alterar'. 'motivo' é o motivo informado pelo corretor, se houver. Perguntas "
    "sobre a agenda (o que tenho hoje, quantas visitas...) são 'consultar'."
)


@dataclass
class AcaoPendente:
    """Ação aguardando confirmação (ou complemento) do corretor."""

    tipo: str  # "cancelar" | "alterar"
    agendamento_id: Optional[str] = None  # None = ainda falta escolher qual
    nova_data_hora: Optional[datetime] = None
    novo_texto: Optional[str] = None  # None (em "alterar") = ainda falta o horário
    motivo: Optional[str] = None


@dataclass
class RespostaGestao:
    texto: str
    acao_pendente: Optional[AcaoPendente] = None
    agenda_alterada: bool = False


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.lower()


class GestaoAgendaCorretorAgent:
    def __init__(
        self,
        llm_provider: ILLMProvider,
        agenda_service: AgendaService,
        consulta_agent: ConsultaAgendaAgent,
    ) -> None:
        self._llm = llm_provider
        self._servico = agenda_service
        self._consulta = consulta_agent

    # ------------------------------------------------------------------ API
    def processar(
        self, corretor: Corretor, mensagem: str, pendente: Optional[AcaoPendente] = None
    ) -> RespostaGestao:
        ativos = self._servico.agendamentos_ativos(corretor.id)

        if pendente is not None:
            resposta = self._continuar_pendente(corretor, mensagem, pendente, ativos)
            if resposta is not None:
                return resposta

        interpretacao = self._interpretar(mensagem, ativos)
        if interpretacao["acao"] not in ("cancelar", "alterar") or not ativos:
            if interpretacao["acao"] in ("cancelar", "alterar"):
                return RespostaGestao("Você não tem nenhum compromisso ativo para alterar no momento. 🙂")
            return RespostaGestao(self._consulta.responder(corretor, mensagem))

        acao = AcaoPendente(tipo=interpretacao["acao"], motivo=interpretacao["motivo"])
        agendamento = self._resolver_agendamento(mensagem, ativos, interpretacao["numero"], acao.tipo)
        if acao.tipo == "alterar":
            horario = extrair_horario(interpretacao["novo_dia_horario"] or "") or extrair_horario(
                self._parte_destino(mensagem)
            )
            if horario:
                acao.nova_data_hora, acao.novo_texto = horario["data_hora"], horario["texto"]

        if agendamento is None:
            return RespostaGestao(self._pedir_qual(acao.tipo, ativos), acao)
        acao.agendamento_id = agendamento.id
        return self._pedir_complemento_ou_confirmacao(corretor, acao, agendamento)

    # -------------------------------------------------------- fluxo pendente
    def _continuar_pendente(
        self, corretor: Corretor, mensagem: str, pendente: AcaoPendente, ativos: list[Agendamento]
    ) -> Optional[RespostaGestao]:
        texto = mensagem.strip().lower()

        # 1) Falta escolher QUAL compromisso.
        if pendente.agendamento_id is None:
            escolhido = self._resolver_agendamento(mensagem, ativos, None, pendente.tipo)
            if escolhido is None:
                if eh_negacao(texto):
                    return RespostaGestao("Tudo bem, não alterei nada. 👍")
                return None  # assunto novo: processa normalmente
            pendente.agendamento_id = escolhido.id
            return self._pedir_complemento_ou_confirmacao(corretor, pendente, escolhido)

        agendamento = next((a for a in ativos if a.id == pendente.agendamento_id), None)
        if agendamento is None:
            return RespostaGestao("Esse compromisso não está mais ativo na sua agenda, então não alterei nada.")

        # 2) Falta o novo horário (remarcação).
        if pendente.tipo == "alterar" and pendente.novo_texto is None:
            horario = extrair_horario(mensagem)
            if horario is None:
                if eh_negacao(texto):
                    return RespostaGestao("Tudo bem, não alterei nada. 👍")
                return RespostaGestao(
                    "Não entendi o novo horário 🤔 — pode me dizer o dia e a hora? "
                    "Ex.: \"sexta às 14h\" ou \"amanhã às 10h\".",
                    pendente,
                )
            pendente.nova_data_hora, pendente.novo_texto = horario["data_hora"], horario["texto"]
            return self._pedir_complemento_ou_confirmacao(corretor, pendente, agendamento)

        # 3) Aguardando "sim"/"não".
        if eh_negacao(texto):
            return RespostaGestao("Tudo bem, não alterei nada. 👍")
        if eh_aceite(texto):
            return self._executar(corretor, pendente, agendamento)
        # Nem "sim" nem "não": o corretor mudou de assunto — a ação pendente
        # é descartada (nada foi alterado) e a mensagem é tratada como nova.
        return None

    def _pedir_complemento_ou_confirmacao(
        self, corretor: Corretor, acao: AcaoPendente, agendamento: Agendamento
    ) -> RespostaGestao:
        descricao = self._descrever(agendamento)
        if acao.tipo == "alterar" and acao.novo_texto is None:
            return RespostaGestao(f"Claro! Para quando você quer remarcar a {descricao}?", acao)

        if acao.tipo == "cancelar":
            motivo = f" (motivo: {acao.motivo})" if acao.motivo else ""
            texto = f"Só para confirmar: **cancelar** a {descricao}{motivo}?"
        else:
            motivo = f" (motivo: {acao.motivo})" if acao.motivo else ""
            texto = f"Só para confirmar: **remarcar** a {descricao} para **{acao.novo_texto}**{motivo}?"
            conflitos = self._servico.conflitos(corretor.id, acao.nova_data_hora, ignorar_id=agendamento.id)
            if conflitos:
                texto += (
                    f"\n\n⚠️ Atenção: você já tem {self._descrever(conflitos[0])} nesse horário."
                )
        texto += "\n\nResponda **sim** para confirmar ou **não** para manter como está."
        return RespostaGestao(texto, acao)

    def _executar(self, corretor: Corretor, acao: AcaoPendente, agendamento: Agendamento) -> RespostaGestao:
        cliente = agendamento.cliente_nome or "o cliente"
        if acao.tipo == "cancelar":
            resultado = self._servico.cancelar_pelo_corretor(agendamento.id, corretor.id, acao.motivo)
            if not resultado.sucesso:
                return RespostaGestao(resultado.mensagem)
            return RespostaGestao(
                f"Pronto! ✅ Cancelei a {self._descrever(agendamento)}. {_como_avisei(cliente, resultado)} "
                "e ofereci um novo horário.",
                agenda_alterada=True,
            )
        resultado = self._servico.reagendar_pelo_corretor(
            agendamento.id, corretor.id, acao.nova_data_hora, acao.novo_texto, acao.motivo
        )
        if not resultado.sucesso:
            return RespostaGestao(resultado.mensagem)
        return RespostaGestao(
            f"Feito! ✅ Remarquei para **{acao.novo_texto}**. {_como_avisei(cliente, resultado)} "
            "e pedi para confirmar o novo horário.",
            agenda_alterada=True,
        )

    # ------------------------------------------------------- interpretação
    def _interpretar(self, mensagem: str, ativos: list[Agendamento]) -> dict:
        """LLM interpreta; regras simples cobrem falhas (e o modo Mock)."""
        texto = _normalizar(mensagem)
        regra_acao = (
            "cancelar" if _PADRAO_CANCELAR.search(texto)
            else "alterar" if _PADRAO_ALTERAR.search(texto)
            else "consultar"
        )
        motivo_regra = _PADRAO_MOTIVO.search(mensagem.strip())
        resultado = {
            "acao": regra_acao,
            "numero": None,
            "novo_dia_horario": None,
            "motivo": motivo_regra.group(1).strip(" .") if motivo_regra else None,
        }
        if not ativos:
            return resultado

        lista = "\n".join(f"{i}. {self._descrever(a)}" for i, a in enumerate(ativos, start=1))
        try:
            bruto = self._llm.gerar_resposta(
                [
                    {"role": "system", "content": _PROMPT_INTERPRETACAO},
                    {"role": "user", "content": f"Compromissos do corretor:\n{lista}\n\nPedido: {mensagem}"},
                ],
                temperatura=0,
            )
            dados = json.loads(re.search(r"\{.*\}", bruto, re.S).group(0))
        except Exception:  # LLM fora do ar, Mock ou JSON inválido -> fica com as regras
            return resultado

        if dados.get("acao") in ("cancelar", "alterar", "consultar"):
            resultado["acao"] = dados["acao"]
        numero = dados.get("numero")
        if isinstance(numero, int) and 1 <= numero <= len(ativos):
            resultado["numero"] = numero
        for campo in ("novo_dia_horario", "motivo"):
            if isinstance(dados.get(campo), str) and dados[campo].strip():
                resultado[campo] = dados[campo].strip()
        return resultado

    def _resolver_agendamento(
        self, mensagem: str, ativos: list[Agendamento], numero_llm: Optional[int], tipo: str
    ) -> Optional[Agendamento]:
        if numero_llm:
            return ativos[numero_llm - 1]
        texto = _normalizar(mensagem)

        m = _PADRAO_NUMERO.search(texto)
        if m:
            numero = int(m.group(1) or m.group(2))
            if 1 <= numero <= len(ativos):
                return ativos[numero - 1]

        candidatos = ativos
        por_nome = [
            a for a in candidatos
            if a.cliente_nome
            and any(len(p) >= 3 and re.search(rf"\b{p}\b", texto) for p in _normalizar(a.cliente_nome).split())
        ]
        if por_nome:
            candidatos = por_nome

        # Para remarcação, só a parte ANTES de "para" descreve o compromisso atual.
        origem = self._parte_origem(texto) if tipo == "alterar" else texto
        horario = extrair_horario(origem)
        if horario:
            por_data = [
                a for a in candidatos if a.data_hora and a.data_hora.date() == horario["data_hora"].date()
            ]
            if por_data:
                candidatos = por_data

        if "avalia" in texto and any(a.tipo == "avaliacao" for a in candidatos):
            candidatos = [a for a in candidatos if a.tipo == "avaliacao"]
        elif "visita" in texto and any(a.tipo == "visita" for a in candidatos):
            candidatos = [a for a in candidatos if a.tipo == "visita"]
        elif "reuni" in texto and any(a.tipo != "visita" for a in candidatos):
            candidatos = [a for a in candidatos if a.tipo != "visita"]

        if len(candidatos) == 1 and (candidatos is not ativos or len(ativos) == 1):
            return candidatos[0]
        return None

    @staticmethod
    def _parte_origem(texto: str) -> str:
        return re.split(r"\b(?:para|pra|pro)\b", texto, maxsplit=1)[0]

    @staticmethod
    def _parte_destino(texto: str) -> str:
        partes = re.split(r"\b(?:para|pra|pro)\b", texto, maxsplit=1)
        return partes[1] if len(partes) > 1 else ""

    def _pedir_qual(self, tipo: str, ativos: list[Agendamento]) -> str:
        verbo = "cancelar" if tipo == "cancelar" else "remarcar"
        lista = "\n".join(f"{i}. {self._descrever(a)}" for i, a in enumerate(ativos, start=1))
        return f"Qual compromisso você quer {verbo}? Me diga o número:\n\n{lista}"

    @staticmethod
    def _descrever(a: Agendamento) -> str:
        tipo = a.tipo_texto
        imovel = f" — {a.imovel_titulo}" if a.imovel_titulo else ""
        return f"{tipo} do {a.quando_formatado()} com {a.cliente_nome or 'cliente'}{imovel}"


def _como_avisei(cliente: str, resultado) -> str:
    canal = getattr(resultado, "cliente_avisado_por", "")
    if canal == "telegram":
        return f"Já avisei {cliente} agora pelo Telegram"
    if canal == "chat":
        return f"Deixei o aviso no chat de {cliente} (ele(a) também vê assim que voltar a falar comigo)"
    return f"Vou avisar {cliente} assim que voltar a falar comigo"
