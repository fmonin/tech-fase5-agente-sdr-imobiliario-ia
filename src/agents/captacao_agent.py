"""Agente de Captação: o cliente quer VENDER ou colocar para ALUGAR o
próprio imóvel.

Fluxo (determinístico, sem LLM — é um cadastro e um agendamento):
  1. Reconhece o pedido: "quero vender meu apartamento", "quero colocar
     minha casa para alugar", "tenho um imóvel para vender"...
  2. Cadastro rápido, uma pergunta por vez (o que o cliente já disse na
     mesma frase é aproveitado): tipo, quartos, banheiros, vagas, metragem
     (opcional), endereço, bairro (se não veio no endereço) e complemento.
  3. Estimativa PRELIMINAR de valor (faixa), calculada com imóveis
     parecidos da base e o índice de mercado do bairro
     (`src/domain/avaliacao_imovel.py`).
  4. Explica que a avaliação presencial é necessária e agenda a
     "avaliação do imóvel" com o corretor da região (dia/horário do
     cliente, checando conflito na agenda do corretor).
  5. O cliente recebe a confirmação (corretor e telefone) e o corretor é
     avisado na Área do Corretor (🏷️ Nova captação); o compromisso aparece
     na agenda como "Avaliação do imóvel".

O cadastro em andamento fica em `Lead.captacao` (sobrevive entre mensagens
e canais); os concluídos em `Lead.captacoes`.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Optional

from src.agents.intencao_agendamento import eh_aceite, eh_negacao, extrair_horario
from src.agents.state import EstadoConversa
from src.domain.avaliacao_imovel import estimar_valor
from src.domain.entities import Agendamento, Corretor
from src.domain.interfaces import ICRM, IAgendaRepository, ICorretorRepository, IMarketDataRepository, IPropertyRepository
from src.domain.localizacao import MapaBairros

AREA_INVESTIMENTOS = "Investimentos"

# Posse do IMÓVEL ("meu apartamento", "tenho uma casa"), não de qualquer coisa
# ("perto do meu trabalho" não conta).
_POSSE = (r"\b(meu|minha|meus|minhas|nosso|nossa|tenho um|tenho uma|temos um|temos uma)\s+"
          r"(imove\w*|apartamento\w*|apto|ape|casa|sobrado|studio|kitnet|cobertura|sala|loja|terreno|propriedade)\b")
_VENDER = r"\b(vender|vendo|venda|anunciar|avaliar|avaliacao)\b"
_LOCAR = r"\b(alugar|alugo|locar|locacao|aluguel)\b"
_SAIR = re.compile(r"\b(cancela\w*|desist\w*|deixa pra la|esquece|nao quero mais)\b")

_TIPOS = [
    (r"\bcobertura\b", "Cobertura"), (r"\b(studio|estudio)\b", "Studio"),
    (r"\b(kitnet|kitinete|quitinete|kit)\b", "Kitnet"), (r"\b(sala comercial|conjunto comercial|sala)\b", "Sala comercial"),
    (r"\bloja\b", "Loja"), (r"\bterreno\b", "Terreno"), (r"\b(sobrado|casa)\b", "Casa"),
    (r"\b(apartamento|apto|ape|ap)\b", "Apartamento"),
]
_SEM_QUARTOS = {"Sala comercial", "Loja", "Terreno"}
_NUMEROS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6}
_PULAR = re.compile(r"\b(nao sei|pular|pula|sem complemento|nao tem|nenhum|nao lembro|depois)\b")

_PERGUNTAS = {
    "tipo": "Qual é o tipo do imóvel? (apartamento, casa, studio, cobertura, sala comercial...)",
    "quartos": "Quantos quartos ele tem?",
    "banheiros": "E quantos banheiros?",
    "vagas": "Quantas vagas de garagem? (se não tiver, é só dizer \"nenhuma\")",
    "metragem": "Sabe a metragem aproximada (m²)? Se não souber, é só dizer \"não sei\".",
    "endereco": "Qual é o endereço do imóvel? (rua, número e bairro)",
    "bairro": "Em qual bairro fica?",
    "complemento": "Tem complemento? (ex.: apto 52, bloco B — ou \"não tem\")",
}


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def detectar_captacao(texto: str) -> Optional[str]:
    """'venda' | 'locacao' | None. "quero alugar um apê" (inquilino) NÃO conta;
    "quero alugar o MEU apê" (proprietário) conta."""
    t = _normalizar(texto)
    posse = re.search(_POSSE, t)
    if re.search(r"\b(colocar|por|botar|anunciar)\b.{0,30}\b(para|pra|a)\s+(alugar|locacao|aluguel)\b", t) or (
            posse and re.search(_LOCAR, t)):
        return "locacao"
    if re.search(r"\b(colocar|por|botar)\b.{0,30}\ba venda\b", t) or (posse and re.search(_VENDER, t)):
        return "venda"
    if re.search(r"\b(quero|gostaria de|preciso|pretendo|desejo|queria)\s+(vender)\b", t) and not re.search(
            r"\b(compr\w*|invest\w*)\b", t):
        return "venda"
    return None


def _numero_antes(t: str, palavras: str) -> Optional[int]:
    m = re.search(rf"\b(\d{{1,2}}|{'|'.join(_NUMEROS)})\s*(?:{palavras})", t)
    if not m:
        return None
    return int(m.group(1)) if m.group(1).isdigit() else _NUMEROS[m.group(1)]


class CaptacaoImovelAgent:
    def __init__(
        self,
        agenda_repository: IAgendaRepository,
        corretor_repository: ICorretorRepository,
        repositorio_imoveis: IPropertyRepository,
        mapa_bairros: Optional[MapaBairros] = None,
        dados_mercado: Optional[IMarketDataRepository] = None,
        crm: Optional[ICRM] = None,
    ) -> None:
        self._agenda = agenda_repository
        self._corretores = corretor_repository
        self._imoveis = repositorio_imoveis
        self._mapa = mapa_bairros
        self._mercado = dados_mercado
        self._crm = crm

    def __call__(self, estado: EstadoConversa) -> dict:
        mensagem = estado.get("mensagem_usuario", "")
        t = _normalizar(mensagem)
        c = dict(estado.get("captacao") or {})

        if not c:
            finalidade = detectar_captacao(mensagem)
            if not finalidade:
                return {"captacao_respondeu": False}
            c = {"finalidade": finalidade, "etapa": None}
            self._extrair(c, mensagem, etapa=None)
            return self._proxima(c, estado, inicio=True)

        if _SAIR.search(t) and c.get("etapa") != "horario":
            return self._resposta({}, "Tudo bem, deixei o cadastro do imóvel de lado. Se mudar de ideia, é só me chamar! 😉")

        if c.get("etapa") == "horario":
            return self._agendar(c, estado, mensagem)

        antes = {k: c.get(k) for k in _PERGUNTAS}
        self._extrair(c, mensagem, etapa=c.get("etapa"))
        if antes == {k: c.get(k) for k in _PERGUNTAS} and not c.get("_pulou"):
            # Nada aproveitável: assunto novo (ex.: "quero comprar") sai do cadastro.
            if re.search(r"\b(compr\w*|invest\w*|quero alugar um|quero alugar uma)\b", t):
                return {"captacao": {}, "captacao_respondeu": False}
            pergunta = _PERGUNTAS.get(c.get("etapa") or "", "")
            return self._resposta(c, f"Desculpe, não entendi. {pergunta}")
        c.pop("_pulou", None)
        return self._proxima(c, estado)

    # ------------------------------------------------------------ cadastro
    def _extrair(self, c: dict, mensagem: str, etapa: Optional[str]) -> None:
        t = _normalizar(mensagem)
        if not c.get("tipo"):
            c["tipo"] = next((nome for padrao, nome in _TIPOS if re.search(padrao, t)), None)
        for campo, palavras in (("quartos", r"quartos?|dormitorios?|dorms?|qts?"),
                                ("banheiros", r"banheiros?|wcs?|banhos?"), ("vagas", r"vagas?")):
            if c.get(campo) is None:
                valor = _numero_antes(t, palavras)
                if valor is not None:
                    c[campo] = valor
        if c.get("vagas") is None and re.search(r"\b(sem vaga|nenhuma vaga|nao tem vaga)\b", t):
            c["vagas"] = 0
        if c.get("metragem") is None:
            m = re.search(r"(\d{2,4}(?:[.,]\d+)?)\s*(m2|m²|metros|mt)", t)
            if m:
                c["metragem"] = float(m.group(1).replace(",", "."))
        # Resposta curta à pergunta da vez ("2", "nenhuma", "não sei"...)
        if etapa in ("quartos", "banheiros", "vagas") and c.get(etapa) is None:
            m = re.fullmatch(r"\D{0,15}?(\d{1,2})\D{0,15}", t) or None
            palavra = next((n for p, n in _NUMEROS.items() if re.search(rf"\b{p}\b", t)), None)
            if m:
                c[etapa] = int(m.group(1))
            elif palavra is not None:
                c[etapa] = palavra
            elif etapa == "vagas" and re.search(r"\b(nenhuma|nao tem|sem|zero)\b", t):
                c["vagas"] = 0
        elif etapa == "metragem" and c.get("metragem") is None:
            m = re.search(r"(\d{2,4}(?:[.,]\d+)?)", t)
            if m:
                c["metragem"] = float(m.group(1).replace(",", "."))
            elif _PULAR.search(t):
                c["metragem"] = 0.0  # 0 = cliente não sabe
                c["_pulou"] = True
        elif etapa == "endereco" and not c.get("endereco") and len(mensagem.strip()) >= 5:
            c["endereco"] = mensagem.strip()
            c["bairro"] = c.get("bairro") or self._bairro_no_texto(mensagem)
        elif etapa == "bairro" and not c.get("bairro"):
            c["bairro"] = self._bairro_no_texto(mensagem) or mensagem.strip().title()
        elif etapa == "complemento" and c.get("complemento") is None:
            c["complemento"] = "" if _PULAR.search(t) or eh_negacao(mensagem) else mensagem.strip()
            c["_pulou"] = True
        if not c.get("bairro") and etapa not in ("endereco", "bairro"):
            c["bairro"] = self._bairro_no_texto(mensagem)

    def _bairro_no_texto(self, texto: str) -> Optional[str]:
        if not self._mapa:
            return None
        t = f" {_normalizar(texto)} "
        candidatos = [nome for chave, nome in self._mapa.nomes.items() if f" {chave} " in re.sub(r"[,.;-]", " ", t)]
        return max(candidatos, key=len) if candidatos else None

    def _faltando(self, c: dict) -> Optional[str]:
        for campo in ("tipo", "quartos", "banheiros", "vagas", "metragem", "endereco", "bairro", "complemento"):
            if campo == "quartos" and c.get("tipo") in _SEM_QUARTOS:
                continue
            if c.get(campo) is None or (campo in ("tipo", "endereco", "bairro") and not c.get(campo)):
                return campo
        return None

    def _proxima(self, c: dict, estado: EstadoConversa, inicio: bool = False) -> dict:
        faltando = self._faltando(c)
        objetivo = "vender" if c["finalidade"] == "venda" else "colocar para alugar"
        if faltando:
            c["etapa"] = faltando
            abertura = (f"Que ótimo que você quer {objetivo} o seu imóvel com a gente! 🏡 Vou fazer um cadastro "
                        "rápido para um corretor avaliar. " if inicio else "Anotado! ")
            return self._resposta(c, abertura + _PERGUNTAS[faltando])

        # Cadastro completo: ficha + estimativa + pedido do horário da avaliação
        corretor = self._escolher_corretor(c.get("bairro"))
        c["corretor_id"] = corretor.id if corretor else None
        c["corretor_nome"] = corretor.nome if corretor else None
        c["zona"] = (self._mapa.zona_de(c["bairro"]) if self._mapa and c.get("bairro") else None) or ""
        estimativa = estimar_valor(
            c["finalidade"], c.get("tipo"), c.get("quartos"), c.get("metragem") or None, c.get("bairro"),
            self._imoveis.listar_todos(), self._mapa,
            self._mercado.obter_indicadores() if self._mercado else None,
        )
        c["estimativa"] = estimativa.texto(c["finalidade"]) if estimativa else None
        c["etapa"] = "horario"
        valor = (f"📊 Estimativa preliminar: {c['estimativa']}.\nÉ só uma referência: o valor certo depende do "
                 "estado de conservação, andar, vista e documentação — por isso a avaliação presencial é necessária."
                 if estimativa else
                 "📊 Ainda não tenho imóveis parecidos suficientes na base para estimar o valor — o corretor faz "
                 "essa avaliação na visita.")
        com = f"o(a) corretor(a) {corretor.nome}, especialista na região," if corretor else "um dos nossos corretores"
        texto = (f"Perfeito, cadastro feito! ✅\n\n🏷️ Seu imóvel: {self._ficha(c)}\n\n{valor}\n\n"
                 f"Para dar seguimento, {com} precisa visitar o imóvel para fazer a avaliação e combinar os "
                 "próximos passos (fotos, documentação e anúncio). Qual dia e horário ficam bons para você? "
                 "(ex.: \"sábado às 10h\" ou \"dia 12 às 15h\")")
        return self._resposta(c, texto)

    @staticmethod
    def _ficha(c: dict) -> str:
        partes = [c.get("tipo") or "Imóvel"]
        if c.get("quartos") is not None and c.get("tipo") not in _SEM_QUARTOS:
            partes.append(f"{c['quartos']} quarto(s)")
        if c.get("banheiros") is not None:
            partes.append(f"{c['banheiros']} banheiro(s)")
        if c.get("vagas") is not None:
            partes.append("sem vaga" if c["vagas"] == 0 else f"{c['vagas']} vaga(s)")
        if c.get("metragem"):
            partes.append(f"{c['metragem']:.0f} m²")
        endereco = c.get("endereco") or ""
        if c.get("complemento"):
            endereco += f", {c['complemento']}"
        if c.get("bairro") and _normalizar(c["bairro"]) not in _normalizar(endereco):
            endereco += f" — {c['bairro']}"
        finalidade = "à venda" if c.get("finalidade") == "venda" else "para locação"
        return f"{' · '.join(partes)} · {endereco} ({finalidade})"

    def _escolher_corretor(self, bairro: Optional[str]) -> Optional[Corretor]:
        zona = (self._mapa.zona_de(bairro) if self._mapa and bairro else None) or ""
        candidatos = self._corretores.buscar_por_zona(zona) if zona else []
        if not candidatos:
            candidatos = [c for c in self._corretores.listar_todos() if AREA_INVESTIMENTOS not in c.zonas_atuacao]
        if not candidatos:
            return None
        return min(candidatos, key=lambda c: (self._agenda.contar_agendamentos_ativos(c.id), c.nome))

    # ------------------------------------------------------------ agendamento
    def _agendar(self, c: dict, estado: EstadoConversa, mensagem: str) -> dict:
        horario = extrair_horario(mensagem)
        if horario is None and eh_aceite(mensagem) and c.get("horario_sugerido"):
            horario = extrair_horario(c["horario_sugerido"])
        if horario is None:
            if eh_negacao(mensagem) and not re.search(r"\d", mensagem):
                return self._resposta({}, "Sem problemas! Seu cadastro ficou salvo. Quando quiser marcar a avaliação, "
                                          "é só me dizer o dia e o horário. 😉", concluir=c)
            c["horario_sugerido"] = "amanhã às 10h"
            return self._resposta(c, "Me diga um dia e horário para a avaliação — por exemplo, \"amanhã às 10h\" "
                                     "ou \"sábado às 9h\". Posso marcar amanhã às 10h?")
        quando = horario["data_hora"]
        if quando < datetime.now():
            return self._resposta(c, "Esse horário já passou 😅 Me diga outro dia e horário, por favor.")
        if c.get("corretor_id") and any(
            a.status != "cancelado" and a.data_hora and abs((a.data_hora - quando).total_seconds()) < 3600
            for a in self._agenda.listar_por_corretor(c["corretor_id"])
        ):
            return self._resposta(c, f"O(a) corretor(a) {c.get('corretor_nome')} já tem um compromisso perto desse "
                                     "horário. Pode me sugerir outro?")
        finalidade = "venda" if c["finalidade"] == "venda" else "locação"
        titulo = f"{c.get('tipo') or 'Imóvel'} para {finalidade} — {c.get('bairro') or c.get('endereco')}"
        detalhes = self._ficha(c) + (f"\nEstimativa preliminar: {c['estimativa']}" if c.get("estimativa") else "")
        agendamento = Agendamento(
            lead_id=estado["lead_id"], quando_sugerido=horario["texto"], tipo="avaliacao", status="confirmado",
            confirmado=True, data_hora=quando, cliente_cpf=estado.get("cliente_cpf"),
            cliente_nome=estado.get("cliente_nome"), corretor_id=c.get("corretor_id"),
            corretor_nome=c.get("corretor_nome"), imovel_titulo=titulo, detalhes=detalhes,
            cliente_notificado=True, corretor_notificado=False,  # aviso 🏷️ na Área do Corretor
        )
        self._agenda.registrar(agendamento)
        if self._crm:
            self._crm.registrar_agendamento(agendamento)
        corretor = self._corretores.buscar_por_id(c["corretor_id"]) if c.get("corretor_id") else None
        contato = f" — telefone {corretor.telefone}" if corretor and corretor.telefone else ""
        local = c.get("endereco", "") + (f", {c['complemento']}" if c.get("complemento") else "")
        texto = (f"✅ Avaliação do imóvel agendada!\n📅 {agendamento.quando_formatado()}\n"
                 f"📍 {local}\n🧑‍💼 Corretor(a): {c.get('corretor_nome') or 'a definir'}{contato}\n\n"
                 "Já avisei o corretor, que vai entrar em contato para confirmar. Se precisar remarcar ou cancelar, "
                 "é só me dizer. Obrigado pela confiança! 🙌")
        resultado = self._resposta({}, texto, concluir={**c, "agendamento_id": agendamento.id})
        resultado["agendamento_novo"] = agendamento
        return resultado

    @staticmethod
    def _resposta(c: dict, texto: str, concluir: Optional[dict] = None) -> dict:
        resultado = {"captacao": {k: v for k, v in c.items() if not k.startswith("_")}, "resposta_agente": texto,
                     "captacao_respondeu": True}
        if concluir:
            resultado["captacao_concluida"] = {k: v for k, v in concluir.items()
                                               if not k.startswith("_") and k != "etapa"}
        return resultado
