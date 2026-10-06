"""Agente de Agenda do Cliente: o próprio cliente CONSULTA, CANCELA ou
REAGENDA os seus agendamentos pelo chat (Streamlit ou Telegram).

Exemplos:
    "quais são meus agendamentos?"         -> lista com corretor e telefone
    "quero cancelar minha visita"          -> pergunta qual (se houver mais de
                                              uma) e pede confirmação (sim/não)
    "remarca a visita do dia 7 para sexta às 15h" -> remarca na hora
    "quero mudar a data da reunião"        -> pergunta o novo dia/horário

Fica na ENTRADA do grafo para clientes já identificados: se a mensagem não
for sobre a agenda dele (nem resposta a uma pergunta deste agente), devolve
`agenda_cliente_respondeu=False` e a conversa segue para o Qualificador.

100% determinístico (sem LLM): mexer na agenda de alguém exige precisão —
qual agendamento, qual horário, com quem. O "estado" da conversa (qual
pergunta foi feita e sobre qual agendamento) vai num marcador invisível
na fala do agente: [[AGENDA:acao,id1,id2...]] (veja `src/domain/midia.py`).

Precedência: logo depois de o Agendador PROPOR um horário, "quero mudar o
horário" continua com o Agendador (é a proposta, não um agendamento
existente). Cancelar vale sempre.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from src.agents.agendas_cliente import agendamentos_ativos_do_cliente, descrever_agendas_do_cliente
from src.agents.intencao_agendamento import eh_aceite, eh_negacao, extrair_horario, ha_pergunta_de_horario_aberta
from src.agents.state import EstadoConversa
from src.domain.entities import Agendamento
from src.domain.feedback_visita import classificar_feedback, registro_de_feedback
from src.domain.interfaces import ICRM, IAgendaRepository, ICorretorRepository, IPropertyRepository

_CANCELAR = re.compile(r"\b(cancel\w*|desmarc\w*)\b")
_CANCELAR_FRACO = re.compile(r"\b(exclu\w*|apag\w*|remov\w*|desist\w*|tirar)\b")
_REAGENDAR = re.compile(r"\b(remarc\w*|reagend\w*|adiar|adia|antecip\w*)\b")
_MUDAR = re.compile(r"\b(mud\w*|alter\w*|troc\w*|mover|passar|passa)\b")
_SOBRE_AGENDA = re.compile(r"\b(agend\w*|visita\w*|reuni\w*|compromisso\w*|horario\w*|data|dia|encontro|agenda)\b")
_CONSULTAR = re.compile(
    r"\b(meus agendamentos|minhas visitas|minhas reunioes|minha agenda|meus compromissos|meus horarios"
    r"|o que (eu )?tenho (marcado|agendado)|tenho (algum|alguma|agendamento|visita|reuniao) (marcad|agendad)\w*)\b"
)
_MARCADOR = re.compile(r"\[\[AGENDA:([A-Za-z0-9_,\-]+)\]\]")
_MARCADOR_POS_VISITA = re.compile(r"\[\[POSVISITA:([A-Za-z0-9_\-]+)\]\]")
_ORDINAIS = {"primeir": 1, "segund": 2, "terceir": 3, "quart": 4, "quint": 5}


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def _marcar(texto: str, acao: str, ids: list[str]) -> str:
    return f"{texto}\n\n[[AGENDA:{','.join([acao, *ids])}]]"


def _descricao(a: Agendamento) -> str:
    tipo = a.tipo_texto
    com = f" com {a.corretor_nome}" if a.corretor_nome else ""
    imovel = f" ({a.imovel_titulo})" if a.imovel_titulo else ""
    return f"{tipo} {a.quando_formatado()}{com}{imovel}"


class AgendaClienteAgent:
    def __init__(
        self,
        agenda_repository: IAgendaRepository,
        corretor_repository: ICorretorRepository | None = None,
        crm: ICRM | None = None,
        repositorio_imoveis: IPropertyRepository | None = None,
    ) -> None:
        self._imoveis = repositorio_imoveis
        self._agenda = agenda_repository
        self._corretores = corretor_repository
        self._crm = crm

    # ------------------------------------------------------------------ fluxo
    def __call__(self, estado: EstadoConversa) -> dict:
        cpf = estado.get("cliente_cpf")
        if not cpf:
            return {"agenda_cliente_respondeu": False}
        mensagem = estado.get("mensagem_usuario", "")
        texto = _normalizar(mensagem)
        ultima = self._ultima_fala_do_agente(estado)
        pendente = _MARCADOR.search(ultima)
        ativos = agendamentos_ativos_do_cliente(self._agenda, cpf)

        resposta = None
        pos_visita = _MARCADOR_POS_VISITA.search(ultima)
        if pos_visita and not (_CANCELAR.search(texto) or _REAGENDAR.search(texto)):
            resposta = self._registrar_feedback(pos_visita.group(1), mensagem)
        if resposta is None and pendente:
            acao, *ids = pendente.group(1).split(",")
            resposta = self._continuar(acao, ids, mensagem, texto, ativos)
        if resposta is None:
            resposta = self._novo_pedido(mensagem, texto, ultima, ativos, cpf)
        if resposta is None:
            return {"agenda_cliente_respondeu": False}
        resposta.setdefault("agenda_cliente_respondeu", True)
        return resposta

    def _novo_pedido(self, mensagem, texto, ultima, ativos, cpf) -> dict | None:
        quer_cancelar = bool(_CANCELAR.search(texto)) or (
            bool(_CANCELAR_FRACO.search(texto)) and bool(_SOBRE_AGENDA.search(texto))
        )
        quer_reagendar = not quer_cancelar and (
            bool(_REAGENDAR.search(texto)) or (bool(_MUDAR.search(texto)) and bool(_SOBRE_AGENDA.search(texto)))
        )
        if quer_reagendar and ha_pergunta_de_horario_aberta(ultima):
            return None  # trocar o horário de uma PROPOSTA é com o Agendador
        if quer_cancelar or quer_reagendar:
            if not ativos:
                if not _SOBRE_AGENDA.search(texto):
                    return None  # "cancelar" sem relação com agenda e sem agendamentos: segue a conversa
                return self._responder(
                    "Não encontrei nenhum agendamento ativo no seu nome. Se quiser marcar uma visita, "
                    "é só me dizer!"
                )
            alvo_txt, horario_txt = self._separar_alvo_e_horario(mensagem)
            alvo = self._escolher(alvo_txt, ativos)
            if quer_cancelar:
                if alvo is None:
                    return self._perguntar_qual("cancelar", ativos)
                return self._pedir_confirmacao_cancelamento(alvo)
            if alvo is None:
                return self._perguntar_qual("reagendar", ativos)
            horario = extrair_horario(horario_txt) if horario_txt else None
            return self._reagendar(alvo, horario, ativos)
        if _CONSULTAR.search(texto):
            agendas = descrever_agendas_do_cliente(self._agenda, self._corretores, cpf)
            if not agendas:
                return self._responder("Você não tem nenhum agendamento ativo no momento. Se quiser marcar uma visita, é só me dizer!")
            return self._responder(f"{agendas}\n\nSe quiser remarcar ou cancelar algum, é só me dizer.")
        return None

    def _continuar(self, acao, ids, mensagem, texto, ativos) -> dict | None:
        por_id = {a.id: a for a in ativos}
        candidatos = [por_id[i] for i in ids if i in por_id]
        if acao == "confirmar_cancelar":
            if not candidatos:
                return None
            if eh_negacao(mensagem) and not _CANCELAR.search(texto):
                return self._responder(f"Tudo bem, mantive sua {_descricao(candidatos[0])}. 😉")
            if eh_aceite(mensagem) or re.search(r"\b(pode cancelar|cancela|confirmo|isso)\b", texto):
                return self._cancelar(candidatos[0])
            return None
        if acao in ("escolher_cancelar", "escolher_reagendar"):
            if not candidatos:
                return None
            if eh_negacao(mensagem) and not re.search(r"\d", texto):
                return self._responder("Tudo bem, não alterei nada na sua agenda.")
            alvo_txt, horario_txt = self._separar_alvo_e_horario(mensagem)
            alvo = self._numero_escolhido(alvo_txt, candidatos) or self._escolher(alvo_txt, candidatos, exigir_pista=True)
            if alvo is None:
                return None
            if acao == "escolher_cancelar":
                return self._pedir_confirmacao_cancelamento(alvo)
            horario = extrair_horario(horario_txt) if horario_txt else None
            return self._reagendar(alvo, horario, ativos)
        if acao == "horario_reagendar":
            if not candidatos:
                return None
            horario = extrair_horario(mensagem)
            if horario:
                return self._reagendar(candidatos[0], horario, ativos)
            if eh_negacao(mensagem):
                return self._responder(f"Tudo bem, mantive sua {_descricao(candidatos[0])}.")
            return None
        return None

    def _registrar_feedback(self, agendamento_id: str, mensagem: str) -> dict | None:
        """Resposta do cliente à pergunta pós-visita ("o que achou?")."""
        a = self._agenda.buscar_por_id(agendamento_id)
        if a is None or len(mensagem.strip()) < 2:
            return None
        sentimento, motivo = classificar_feedback(mensagem)
        a.feedback_cliente = mensagem.strip()
        a.corretor_notificado = False  # o corretor vê o retorno na Área do Corretor
        self._agenda.registrar(a)
        if self._crm:
            self._crm.registrar_agendamento(a)
        corretor = a.corretor_nome or "o corretor"
        resultado: dict
        if sentimento == "negativo":
            imovel = None
            if self._imoveis and a.imovel_id:
                imovel = next((im for im in self._imoveis.listar_todos() if im.id == a.imovel_id), None)
            ajuste = {
                "Preço": "mais em conta", "Condomínio caro": "com condomínio menor", "Tamanho": "maiores",
                "Localização": "em outros bairros", "Estado do imóvel": "mais conservados",
            }.get(motivo or "", "mais parecidas com o que você procura")
            resultado = self._responder(
                f"Obrigado pela sinceridade! 🙏 Já passei o seu retorno para o(a) {corretor}. "
                f"Quando quiser, me peça \"outras opções\" que eu já busco imóveis {ajuste}."
            )
            resultado["feedback_visita_novo"] = registro_de_feedback(imovel, motivo, a.imovel_id)
        elif sentimento == "positivo":
            resultado = self._responder(
                f"Que ótimo! 🎉 Já passei para o(a) {corretor}, que vai falar com você sobre os próximos "
                "passos (proposta, documentação e financiamento). Qualquer dúvida, é só me chamar!"
            )
        else:
            resultado = self._responder(f"Obrigado pelo retorno! Já passei para o(a) {corretor}. 😉")
        return resultado

    # ------------------------------------------------------------------ ações
    def _perguntar_qual(self, acao: str, ativos: list[Agendamento]) -> dict:
        verbo = "cancelar" if acao == "cancelar" else "remarcar"
        lista = descrever_agendas_do_cliente(self._agenda, self._corretores, ativos[0].cliente_cpf) or ""
        texto = f"{lista}\n\nQual deles você quer {verbo}? Pode me dizer o número (ex.: \"o 1\")."
        return self._responder(_marcar(texto, f"escolher_{acao}", [a.id for a in ativos]))

    def _pedir_confirmacao_cancelamento(self, alvo: Agendamento) -> dict:
        texto = f"Você confirma o cancelamento da sua {_descricao(alvo)}? (sim/não)"
        return self._responder(_marcar(texto, "confirmar_cancelar", [alvo.id]))

    def _cancelar(self, alvo: Agendamento) -> dict:
        antes = alvo.quando_sugerido
        alvo.status = "cancelado"
        alvo.confirmado = False
        alvo.cancelado_por = "cliente"
        alvo.cancelado_em = datetime.utcnow()
        alvo.cliente_notificado = True  # foi o próprio cliente: não há o que avisar a ele
        alvo.corretor_notificado = False  # ...mas o corretor precisa saber
        self._salvar(alvo)
        com = f" Já avisei o(a) corretor(a) {alvo.corretor_nome}." if alvo.corretor_nome else ""
        return self._responder(
            f"Pronto, cancelei sua {_descricao(alvo)}.{com} Se quiser marcar outro dia, é só me pedir!",
            alvo, antes,
        )

    def _reagendar(self, alvo: Agendamento, horario: dict | None, ativos: list[Agendamento]) -> dict:
        if horario is None:
            texto = (f"Claro! Para qual dia e horário você quer remarcar a sua {_descricao(alvo)}? "
                     "Pode me dizer, por exemplo, \"sexta às 15h\" ou \"dia 12 às 10h\".")
            return self._responder(_marcar(texto, "horario_reagendar", [alvo.id]))
        nova = horario["data_hora"]
        if nova < datetime.now():
            return self._responder(_marcar(
                "Esse horário já passou 😅 Me diga outro dia e horário, por favor.", "horario_reagendar", [alvo.id]))
        if alvo.corretor_id and self._corretor_ocupado(alvo, nova):
            return self._responder(_marcar(
                f"O(a) corretor(a) {alvo.corretor_nome} já tem um compromisso perto desse horário "
                f"({horario['texto']}). Pode me sugerir outro dia ou horário?", "horario_reagendar", [alvo.id]))
        anterior, antes = alvo.quando_formatado(), alvo.quando_sugerido
        alvo.horario_anterior = anterior
        alvo.data_hora = nova
        alvo.quando_sugerido = horario["texto"]
        alvo.status = "confirmado"
        alvo.confirmado = True
        alvo.motivo_alteracao = "Remarcado pelo cliente"
        alvo.cliente_notificado = True  # mudança feita pelo próprio cliente
        alvo.corretor_notificado = False  # o corretor é avisado na Área do Corretor
        self._salvar(alvo)
        tipo = alvo.tipo_texto
        com = f" com o(a) corretor(a) {alvo.corretor_nome}" if alvo.corretor_nome else ""
        return self._responder(
            f"Prontinho! Remarquei sua {tipo}{com}: de {anterior} para {alvo.quando_formatado()}. "
            "Já avisei o corretor do novo horário. Posso ajudar em mais alguma coisa?",
            alvo, antes,
        )

    # ------------------------------------------------------------------ apoio
    def _corretor_ocupado(self, alvo: Agendamento, nova: datetime) -> bool:
        return any(
            a.id != alvo.id and a.status != "cancelado" and a.data_hora
            and abs((a.data_hora - nova).total_seconds()) < 3600
            for a in self._agenda.listar_por_corretor(alvo.corretor_id)
        )

    def _salvar(self, alvo: Agendamento) -> None:
        self._agenda.registrar(alvo)
        if self._crm:
            self._crm.registrar_agendamento(alvo)

    @staticmethod
    def _responder(texto: str, alterado: Agendamento | None = None, antes: str | None = None) -> dict:
        resultado: dict = {"resposta_agente": texto, "agenda_cliente_respondeu": True}
        if alterado is not None:
            resultado["agendamento_alterado_pelo_cliente"] = alterado
            resultado["agendamento_alterado_antes"] = antes
        return resultado

    @staticmethod
    def _ultima_fala_do_agente(estado: EstadoConversa) -> str:
        for m in reversed(estado.get("historico_mensagens") or []):
            if m.get("role") == "assistant":
                return str(m.get("content", ""))
        return ""

    @staticmethod
    def _separar_alvo_e_horario(mensagem: str) -> tuple[str, str]:
        """'remarcar a visita do dia 7 para sexta às 15h' -> ('remarcar a visita do dia 7', 'sexta às 15h')."""
        partes = re.split(r"\b(?:para|pra|pro)\b", mensagem, maxsplit=1, flags=re.IGNORECASE)
        if len(partes) == 2 and extrair_horario(partes[1]):
            return partes[0], partes[1]
        return mensagem, mensagem

    @staticmethod
    def _numero_escolhido(texto: str, candidatos: list[Agendamento]) -> Agendamento | None:
        t = _normalizar(texto)
        for raiz, n in _ORDINAIS.items():
            if re.search(rf"\b{raiz}[oa]\b", t) and n <= len(candidatos):
                return candidatos[n - 1]
        numeros = [int(n) for n in re.findall(r"(?<![\d/:])\b(\d)\b(?!\s*(?:/|:|h\b|hs?\b|hrs?\b|horas?))", t)]
        if len(numeros) == 1 and 1 <= numeros[0] <= len(candidatos) and not re.search(r"\bdia\s+\d", t):
            return candidatos[numeros[0] - 1]
        return None

    def _escolher(self, texto: str, ativos: list[Agendamento], exigir_pista: bool = False) -> Agendamento | None:
        """Qual agendamento o cliente citou: pelo dia, pelo corretor, pelo tipo
        (visita/reunião) ou pelo imóvel. Com um único ativo, é ele."""
        if len(ativos) == 1 and not exigir_pista:
            return ativos[0]
        t = _normalizar(texto)
        candidatos = list(ativos)

        def filtrar(condicao) -> None:
            nonlocal candidatos
            filtrados = [a for a in candidatos if condicao(a)]
            if filtrados:
                candidatos = filtrados

        usou_pista = False
        quando = extrair_horario(texto)
        if quando:
            d = quando["data_hora"]
            if any(a.data_hora and a.data_hora.date() == d.date() for a in candidatos):
                filtrar(lambda a: bool(a.data_hora) and a.data_hora.date() == d.date())
                usou_pista = True
        nomes = [a for a in candidatos if a.corretor_nome and _normalizar(a.corretor_nome.split()[0]) in t.split()]
        if nomes:
            filtrar(lambda a: a in nomes)
            usou_pista = True
        if re.search(r"\bavalia", t):
            filtrar(lambda a: a.tipo == "avaliacao")
            usou_pista = True
        elif re.search(r"\bvisita", t):
            filtrar(lambda a: a.tipo == "visita")
            usou_pista = True
        elif re.search(r"\breuni", t):
            filtrar(lambda a: a.tipo == "reuniao")
            usou_pista = True
        imoveis = [a for a in candidatos if a.imovel_titulo and any(
            len(p) > 3 and p in t for p in _normalizar(a.imovel_titulo).split())]
        if imoveis and len(imoveis) < len(candidatos):
            filtrar(lambda a: a in imoveis)
            usou_pista = True
        if len(candidatos) == 1 and (usou_pista or not exigir_pista):
            return candidatos[0]
        if not exigir_pista and len(ativos) == 1:
            return ativos[0]
        return None
