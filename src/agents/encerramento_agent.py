"""Agente de Encerramento: o CLIENTE encerra o atendimento quando quiser.

Gatilhos: "encerrar atendimento", "pode finalizar", "tchau", "por hoje é só",
o comando /encerrar no Telegram ou o botão "✅ Encerrar atendimento" no chat
web. Funciona em qualquer etapa (até no meio de um cadastro), porque fica na
ENTRADA do grafo.

Ao encerrar:
  • despede-se com um resumo curto (o que o cliente procurava e as visitas
    marcadas — para ele não esquecer);
  • pede uma nota opcional de 1 a 5 (satisfação, vai para o Painel);
  • marca o lead como encerrado: se NÃO ficou nada pela metade, o follow-up
    não insiste; se ficou um negócio pendente (compra, aluguel, investimento,
    venda do próprio imóvel), o follow-up tenta trazê-lo de volta com
    argumentos reais — a menos que ele peça para não receber mensagens
    ("pare de me mandar mensagens" -> `nao_contatar`);
  • o histórico fica salvo: na próxima mensagem o atendimento reabre e o
    cliente continua de onde parou.

Determinístico (sem LLM).
"""
from __future__ import annotations

import re
import unicodedata

from src.agents.agendas_cliente import agendamentos_ativos_do_cliente
from src.agents.contexto_conversa import descrever_interesse
from src.agents.state import EstadoConversa
from src.domain.interfaces import IAgendaRepository

MARCADOR_ENCERRADO = "[[ENCERRADO:1]]"
_PEDIDO = re.compile(
    r"(\b(encerr\w*|finaliz\w*|termin\w*|fech\w*)\s+(o |a |esse |este |essa |meu |nosso )?(atendimento|conversa|chat|papo)\b"
    r"|^\W*/?(encerrar|finalizar|sair|tchau|tchau tchau|ate mais|ate logo|falou|flw)\W*$"
    r"|^\W*(pode )?(encerrar|finalizar)\b"
    r"|\b(por hoje e so|era so isso|nao preciso de mais nada|so isso,? obrigad\w*|isso e tudo,? obrigad\w*)\b)"
)


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode().strip()


def quer_parar_contato(texto: str) -> bool:
    t = _normalizar(texto)
    return bool(re.search(r"\b(pare|para|parem) de (me )?(mandar|enviar|chamar|ligar)\b", t)) or bool(
        re.search(r"\b(nao quero (mais )?(receber|contato|mensage\w*)|nao me (mande|mandem|envie|enviem)\b|"
                  r"remov\w* meu (contato|numero|cadastro)|nao tenho (mais )?interesse|descadastr\w*|"
                  r"sair da lista)", t))


def quer_encerrar(texto: str) -> bool:
    return bool(_PEDIDO.search(_normalizar(texto))) or quer_parar_contato(texto)


def nota_do_atendimento(texto: str) -> int | None:
    m = re.fullmatch(r"\D{0,10}?([1-5])(\s*(estrelas?|de 5|/5))?\W*", _normalizar(texto))
    return int(m.group(1)) if m else None


def aguardando_nota(estado: EstadoConversa) -> bool:
    ultima = next((m.get("content", "") for m in reversed(estado.get("historico_mensagens") or [])
                   if m.get("role") == "assistant"), "")
    return MARCADOR_ENCERRADO in ultima


class EncerramentoAgent:
    def __init__(self, agenda_repository: IAgendaRepository | None = None) -> None:
        self._agenda = agenda_repository

    def __call__(self, estado: EstadoConversa) -> dict:
        mensagem = estado.get("mensagem_usuario", "")
        if aguardando_nota(estado) and not quer_encerrar(mensagem):
            nota = nota_do_atendimento(mensagem)
            return {
                "atendimento_encerrado": True,
                "nota_atendimento": nota,
                "resposta_agente": "Obrigado pela avaliação! 💙 Quando precisar, é só mandar uma mensagem por aqui.",
            }

        nome = (estado.get("cliente_nome") or "").split(" ")[0]
        if quer_parar_contato(mensagem):
            return {
                "atendimento_encerrado": True,
                "nao_contatar": True,
                "captacao": {},
                "resposta_agente": (f"Entendido{', ' + nome if nome else ''}! Não vou mais te enviar mensagens. 🙏 "
                                    "Se um dia precisar, é só me chamar por aqui — seu histórico continua salvo."),
            }
        linhas = [f"Atendimento encerrado ✅ Foi um prazer te ajudar{', ' + nome if nome else ''}!"]
        interesse = descrever_interesse(dict(estado))
        if interesse:
            linhas.append(f"📝 Ficou registrado o seu interesse: {interesse}.")
        for a in agendamentos_ativos_do_cliente(self._agenda, estado.get("cliente_cpf")):
            com = f" com {a.corretor_nome}" if a.corretor_nome else ""
            linhas.append(f"📅 Não esqueça: {a.tipo_texto} {a.quando_formatado()}{com}.")
        linhas.append("Seu histórico fica salvo — quando quiser voltar, é só mandar uma mensagem que eu continuo de onde paramos.")
        linhas.append("Antes de ir: de 1 a 5, que nota você dá para este atendimento? (opcional)")
        if estado.get("captacao"):
            linhas.insert(1, "🏷️ O cadastro do seu imóvel ficou salvo — quando quiser, a gente termina em 1 minuto.")
        return {
            "atendimento_encerrado": True,
            "resposta_agente": "\n".join(linhas) + f"\n\n{MARCADOR_ENCERRADO}",
        }
