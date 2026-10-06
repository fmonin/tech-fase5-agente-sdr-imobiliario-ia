"""Agente de Identificação (cadastro de cliente).

Responsabilidade única: reconhecer QUEM está conversando antes de qualquer
outro agente entrar em ação — é o primeiro nó do grafo (veja o roteamento
de entrada em `src/agents/graph.py`), e só deixa a conversa avançar para o
`QualificadorAgent` depois que o cliente está identificado.

Fluxo (uma pequena "máquina de estados" conversacional):
    1. Se a mensagem do lead já contém um CPF válido, pulamos direto pra
       identificação (não precisa perguntar "é seu primeiro contato?" se o
       cliente já adiantou o CPF — mais natural).
       a) Se aquele CPF já existe na base → é um cliente recorrente:
          recuperamos o lead (e todo o histórico) dele.
       b) Se não existe → é um cliente novo informando o CPF; falta só o
          nome (se ele já mandou junto, ótimo; senão perguntamos).
    2. Se já sabemos o CPF de um turno anterior mas ainda falta o nome,
       tratamos a mensagem atual como sendo o nome.
    3. Caso contrário (ainda não sabemos nada), perguntamos se é o
       primeiro contato — e, dependendo da resposta, pedimos nome+CPF ou
       só o CPF.

Uma decisão de design importante: este agente NÃO usa o LLM (veja o
docstring de `src/domain/cpf.py` para o porquê) — é 100% determinístico,
o que o torna fácil de testar e, mais importante, confiável para dado de
identidade (nunca "inventa" um nome ou CPF).
"""
from __future__ import annotations

import re

from src.agents.agendas_cliente import descrever_agendas_do_cliente
from src.agents.contexto_conversa import PERGUNTA_CONTINUAR, descrever_interesse
from src.agents.state import EstadoConversa
from src.config import settings
from src.domain.cpf import extrair_cpf_valido, formatar_cpf, texto_contem_padrao_de_cpf
from src.agents.intencao_agendamento import PERGUNTA_CONFIRMACAO_HORARIO
from src.domain.entities import Agendamento
from src.domain.interfaces import IAgendaRepository, ICorretorRepository, ILeadRepository

# A pergunta feita ao lead é "você já falou com a gente antes?" — então
# "sim" significa cliente RECORRENTE (só falta o CPF) e "não"/"nunca"/
# "primeira vez" significam cliente NOVO (falta nome + CPF). É fácil
# inverter esse mapeamento sem querer, então deixamos explícito aqui perto
# um do outro, com o texto da pergunta como comentário de referência.
_PALAVRAS_JA_E_CLIENTE = {
    "sim", "já falei", "ja falei", "voltei", "retornando", "de novo", "outra vez", "novamente",
}
_PALAVRAS_PRIMEIRO_CONTATO = {
    "não", "nao", "primeira vez", "nunca", "não conheço", "nao conheco", "novo por aqui",
}

# Palavras que só respondem a pergunta "já é cliente?" (sim/não) e que, se
# vierem coladas com o nome na mesma mensagem (ex.: "Sim, João da Silva,
# CPF..."), não podem "vazar" para dentro do nome extraído.
_PALAVRAS_CONFIRMACAO = ("sim", "não", "nao", "já", "ja")

_PREFIXOS_PARA_REMOVER = (
    "meu nome é", "meu nome e", "me chamo", "nome:", "sou o", "sou a", "sou", "cpf:", "cpf",
) + _PALAVRAS_CONFIRMACAO


class IdentificacaoAgent:
    def __init__(
        self,
        lead_repository: ILeadRepository,
        agenda_repository: IAgendaRepository | None = None,
        corretor_repository: ICorretorRepository | None = None,
    ) -> None:
        self._leads = lead_repository
        self._corretores = corretor_repository
        # Opcional: usado para avisar o cliente recorrente de agendamentos
        # que o corretor cancelou enquanto ele estava fora da conversa.
        self._agenda = agenda_repository

    def __call__(self, estado: EstadoConversa) -> dict:
        mensagem = estado["mensagem_usuario"]
        cpf_ja_conhecido = estado.get("cliente_cpf")
        nome_ja_conhecido = estado.get("cliente_nome")

        # Caso 1: já sabemos o CPF (de um turno anterior), só falta o nome.
        if cpf_ja_conhecido and not nome_ja_conhecido:
            nome = self._extrair_nome(mensagem)
            if nome:
                return self._concluir_cadastro_novo_cliente(cpf_ja_conhecido, nome)
            return {
                "resposta_agente": (
                    "Não consegui identificar seu nome completo, pode me confirmar, "
                    "por favor?"
                )
            }

        # Caso 2: a mensagem já traz um CPF (com ou sem eu ter perguntado).
        if texto_contem_padrao_de_cpf(mensagem):
            cpf = extrair_cpf_valido(mensagem)
            if not cpf:
                return {
                    "resposta_agente": (
                        "Esse CPF não parece válido (o dígito verificador não bate) "
                        "— pode conferir os números e enviar novamente? Se estiver só "
                        "testando o sistema, use um CPF válido de exemplo, como "
                        "111.444.777-35."
                    )
                }

            lead_existente = self._leads.buscar_por_cpf(cpf)
            if lead_existente and lead_existente.id != estado["lead_id"]:
                primeiro_nome = (lead_existente.nome or "").split(" ")[0]
                saudacao = f", {primeiro_nome}" if primeiro_nome else ""
                perfil = lead_existente.perfil
                interesse = descrever_interesse(
                    {
                        "intencao": perfil.intencao.value,
                        "regiao_interesse": perfil.regiao_interesse,
                        "quartos_desejados": perfil.quartos_desejados,
                        "faixa_preco_max": perfil.faixa_preco_max,
                        "ticket_investimento": perfil.ticket_investimento,
                        "expectativa_retorno": perfil.expectativa_retorno,
                    }
                )
                # Agenda ANTES do aviso: o aviso marca os itens como avisados.
                minhas_agendas = self.agendas_do_cliente(cpf)
                aviso_cancelamento = self._avisar_cancelamentos(cpf)
                if aviso_cancelamento:
                    continuidade = (
                        (f" Da última vez falamos sobre {interesse}." if interesse else "")
                        + (f"\n\n{minhas_agendas}" if minhas_agendas else "")
                        + f"\n\n{aviso_cancelamento}"
                    )
                elif minhas_agendas:
                    continuidade = (
                        (f" Da última vez falamos sobre {interesse}." if interesse else "")
                        + f"\n\n{minhas_agendas}\n\n"
                        + (f"Quer continuar de onde paramos {PERGUNTA_CONTINUAR}" if interesse
                           else "Posso te ajudar com mais alguma coisa?")
                    )
                else:
                    continuidade = (
                        f" Da última vez falamos sobre {interesse}. Quer continuar de "
                        f"onde paramos {PERGUNTA_CONTINUAR}"
                        if interesse
                        else " Me conta, no que posso te ajudar agora?"
                    )
                return {
                    "cliente_identificado": True,
                    "cliente_cpf": cpf,
                    "cliente_nome": lead_existente.nome,
                    "lead_id_recuperado": lead_existente.id,
                    "resposta_agente": (
                        f"Que bom te ver de novo{saudacao}! Já recuperei o nosso "
                        f"histórico de conversa.{continuidade}"
                    ),
                }

            # CPF novo (cliente ainda não cadastrado) — tenta pegar o nome
            # na mesma mensagem; se não vier, pergunta em seguida.
            nome = self._extrair_nome(mensagem, cpf=cpf)
            if nome:
                return self._concluir_cadastro_novo_cliente(cpf, nome)
            return {
                "cliente_cpf": cpf,
                "resposta_agente": "Certo! E qual é o seu nome completo, para eu concluir seu cadastro?",
            }

        # Caso 3: ainda não sabemos nada — verifica se já perguntamos "é
        # seu primeiro contato?" nesta conversa (olhando quantas respostas
        # do agente já existem no histórico).
        respostas_do_agente = sum(
            1 for m in estado.get("historico_mensagens", []) if m["role"] == "assistant"
        )

        if respostas_do_agente == 0:
            return {
                "resposta_agente": (
                    f"Olá! Aqui é o {settings.agente_nome} 👋 Antes de começarmos: "
                    "você já falou com a gente antes? Se sim, me diga seu CPF que eu "
                    "recupero nossa conversa. Se não, me diga seu nome completo e CPF "
                    "para eu fazer seu cadastro."
                )
            }

        texto = mensagem.lower()
        # \b (borda de palavra) evita, por exemplo, "ja" achar uma
        # ocorrência escondida dentro de outra palavra.
        if any(re.search(rf"\b{re.escape(p)}\b", texto) for p in _PALAVRAS_JA_E_CLIENTE):
            return {
                "resposta_agente": "Sem problemas! Pode me passar seu CPF para eu recuperar nosso histórico?"
            }
        if any(re.search(rf"\b{re.escape(p)}\b", texto) for p in _PALAVRAS_PRIMEIRO_CONTATO):
            return {
                "resposta_agente": "Perfeito! Pode me passar seu nome completo e CPF para eu fazer seu cadastro?"
            }

        # Resposta ambígua (não bateu com nenhuma das duas listas): pedimos
        # nome + CPF, que é o caminho mais seguro (pedir mais informação em
        # vez de menos) — se o lead já for cliente, o CPF sozinho já basta
        # para o Caso 2 reconhecê-lo no próximo turno.
        return {
            "resposta_agente": "Perfeito! Pode me passar seu nome completo e CPF para eu fazer seu cadastro?"
        }

    def agendas_do_cliente(self, cpf: str) -> str | None:
        return descrever_agendas_do_cliente(self._agenda, self._corretores, cpf)

    def _avisar_cancelamentos(self, cpf: str) -> str | None:
        """Monta o aviso de agendamentos que o corretor CANCELOU ou REMARCOU
        e que o cliente ainda não conhece, e marca-os como avisados (para o
        aviso aparecer uma única vez)."""
        if self._agenda is None:
            return None
        pendentes = self._agenda.listar_avisos_pendentes(cpf)
        if not pendentes:
            return None

        itens = [self._descrever_alteracao(a) for a in pendentes]
        if len(itens) == 1:
            corpo = f"o(a) corretor(a) {itens[0]}"
        else:
            corpo = "houve mudanças nos seus compromissos:\n" + "\n".join(f"- {item}" for item in itens)
        for agendamento in pendentes:
            agendamento.cliente_notificado = True
            self._agenda.registrar(agendamento)

        remarcou = any(a.status != "cancelado" for a in pendentes)
        if remarcou:
            # Mesma pergunta do AgendadorAgent: se o cliente responder "sim",
            # o Agendador confirma o novo horário; se sugerir outro dia, ajusta.
            pergunta = (
                f"{PERGUNTA_CONFIRMACAO_HORARIO} Se preferir outro dia ou horário, "
                "é só me falar que eu ajusto."
            )
        else:
            pergunta = "Quer que eu agende um novo horário para você?"
        return f"⚠️ Um aviso importante: {corpo}\n\nPeço desculpas pelo transtorno! {pergunta}"

    @staticmethod
    def _descrever_alteracao(a: Agendamento) -> str:
        tipo = a.tipo_texto
        imovel = f" ({a.imovel_titulo})" if a.imovel_titulo else ""
        corretor = a.corretor_nome or "responsável"
        if a.status == "cancelado":
            quando = a.horario_anterior or a.quando_formatado()
            motivo = f" Motivo: {a.motivo_cancelamento}." if a.motivo_cancelamento else ""
            return f"{corretor} precisou cancelar a sua {tipo} do {quando}{imovel}.{motivo}"
        motivo = f" Motivo: {a.motivo_alteracao}." if a.motivo_alteracao else ""
        return (
            f"{corretor} precisou remarcar a sua {tipo}{imovel} do {a.horario_anterior} "
            f"para {a.quando_formatado()}.{motivo}"
        )

    @staticmethod
    def _concluir_cadastro_novo_cliente(cpf: str, nome: str) -> dict:
        return {
            "cliente_identificado": True,
            "cliente_cpf": cpf,
            "cliente_nome": nome,
            "resposta_agente": (
                f"Prazer em te conhecer, {nome.split(' ')[0]}! Cadastro concluído "
                f"({formatar_cpf(cpf)}). Me conta: você está buscando comprar, "
                "alugar ou investir em um imóvel?"
            ),
        }

    @staticmethod
    def _extrair_nome(mensagem: str, cpf: str | None = None) -> str | None:
        """Extração determinística e simples do nome (sem LLM — veja o
        docstring do módulo). Remove o CPF (se houver) e frases comuns
        como "meu nome é", e usa o que sobrar."""
        texto = mensagem
        if cpf:
            texto = re.sub(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}", "", texto)

        texto_lower = texto.lower()
        for prefixo in _PREFIXOS_PARA_REMOVER:
            # Usamos \b (borda de palavra) em vez de um simples
            # str.replace: um prefixo curto como "sou" não pode "comer"
            # parte de um nome como "Souza" (que também contém "sou"). O
            # ":?" opcional no final cobre prefixos como "nome:"/"cpf:".
            base = prefixo.rstrip(":")
            texto_lower = re.sub(rf"\b{re.escape(base)}\b:?", "", texto_lower)

        # Remove pontuação sobrando e espaços duplicados.
        candidato = re.sub(r"[.,;:\-]+", " ", texto_lower)
        candidato = re.sub(r"\s+", " ", candidato).strip()

        palavras = [p for p in candidato.split(" ") if p.isalpha()]
        if len(palavras) < 1:
            return None

        return " ".join(p.capitalize() for p in palavras)
