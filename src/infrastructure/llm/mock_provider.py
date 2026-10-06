"""Provedor de LLM "falso" (mock), baseado em regras simples.

Por que ele existe:
    Para rodar e demonstrar o projeto SEM precisar de uma chave do Azure.
    Ele usa palavras-chave para simular entendimento de linguagem natural.
    É claro que a qualidade é muito inferior a um LLM de verdade — mas
    permite testar TODO o fluxo do grafo multiagente (qualificação, busca de
    imóveis, agendamento, follow-up, resumo) de graça e offline.

Trocar para o Azure OpenAI de verdade é só configurar `LLM_PROVIDER=azure`
e as credenciais no .env — nenhuma linha de código dos agentes muda, porque
todos dependem apenas da interface `ILLMProvider`.
"""
from __future__ import annotations

import re

from src.config import settings
from src.domain.interfaces import ILLMProvider

_PALAVRAS_COMPRA = {"comprar", "compra", "adquirir"}
_PALAVRAS_ALUGUEL = {"alugar", "aluguel", "locação", "locacao"}
_PALAVRAS_INVESTIMENTO = {"investir", "investimento", "renda", "retorno"}

_ZONAS = ["zona sul", "zona norte", "zona leste", "zona oeste", "zona central", "centro"]


def _carregar_bairros() -> list[str]:
    """Nomes de bairros conhecidos (data/bairros_sp.json), para o mock
    também reconhecer "Mooca", "Tatuapé"... como região."""
    import json
    from pathlib import Path

    try:
        dados = json.loads((Path(__file__).resolve().parents[3] / "data" / "bairros_sp.json").read_text(encoding="utf-8"))
        return sorted(dados.get("bairros", {}), key=len, reverse=True)
    except (OSError, ValueError):
        return []


_BAIRROS = _carregar_bairros()

# Uma pergunta canônica pronta para cada campo que o EsclarecedorAgent pode
# pedir (veja `EsclarecedorAgent._determinar_campo_faltante`). O campo já
# vem DECIDIDO de forma determinística pelo agente — o mock só precisa
# traduzir o nome do campo numa pergunta, nunca redescobrir sozinho (por
# palavra-chave) o que perguntar, que é justamente o que causava o bug de
# "esquecer" a intenção já identificada quando a última mensagem do lead
# tinha um erro de digitação (ex.: "invetis" em vez de "investir").
_PERGUNTAS_POR_CAMPO = {
    "ticket_investimento": (
        "Perfeito! Para eu te indicar as melhores oportunidades, qual valor "
        "você pretende investir?"
    ),
    "expectativa_retorno": (
        "Show! E qual retorno mensal você espera desse investimento?"
    ),
    "intencao": "Legal! Você está pensando em comprar, alugar ou investir?",
    "regiao_interesse": "Legal! Em qual região ou bairro você está procurando?",
    "quartos_desejados": "Entendido! E quantos quartos você precisa?",
    "faixa_preco": "Certo! Qual orçamento você tem em mente?",
    "urgencia": "Ótimo! E para quando você precisa do imóvel — é algo urgente ou sem pressa?",
}


class MockLLMProvider(ILLMProvider):
    """Gera respostas humanizadas simples e extrai dados por palavras-chave."""

    def gerar_resposta(self, mensagens: list[dict], temperatura: float = 0.4) -> str:
        ultima_mensagem_usuario = next(
            (m["content"] for m in reversed(mensagens) if m["role"] == "user"), ""
        )
        texto_completo = ultima_mensagem_usuario.lower()

        if "introdução curta para estes imóveis" in texto_completo:
            return "Separei estas opções para você:"

        if "imóveis encontrados:" in texto_completo:
            return self._redigir_com_imoveis(ultima_mensagem_usuario)

        if "ficha do imóvel:" in texto_completo:
            ficha = ultima_mensagem_usuario.split("Ficha do imóvel:")[-1].strip()
            return f"Claro! Aqui estão os detalhes:\n\n{ficha}\n\nQuer agendar uma visita a este imóvel?"

        if "follow-up de retomada" in texto_completo:
            nome = ultima_mensagem_usuario.split("Nome do lead:")[-1].splitlines()[0].strip().split(" ")[0]
            saudacao = f"Oi, {nome}!" if nome and nome != "não" else "Oi!"
            fatos = [l[2:] for l in ultima_mensagem_usuario.split("Argumentos")[-1].splitlines() if l.startswith("- ")]
            return f"{saudacao} Olha que bacana: {'; '.join(fatos[:2])}. Vamos retomar de onde paramos?"

        if "follow-up de reengajamento" in texto_completo:
            nome = ultima_mensagem_usuario.split("Nome do lead:")[-1].splitlines()[0].strip().split(" ")[0]
            saudacao = f"Oi, {nome}!" if nome and nome != "não" else "Oi!"
            return f"{saudacao} Passando para saber se ainda posso te ajudar. Quer continuar de onde paramos?"

        if "saudação de login do corretor" in texto_completo:
            return "Olá! Que bom te ver por aqui 👋 " + self._redigir_agenda_corretor(ultima_mensagem_usuario)

        if "agenda do corretor:" in texto_completo:
            return self._redigir_agenda_corretor(ultima_mensagem_usuario)

        if "rascunho do resumo:" in texto_completo:
            # O RecapituladorAgent já monta o resumo de forma determinística;
            # sem LLM real, devolvemos o rascunho como está.
            indice = texto_completo.find("rascunho do resumo:")
            return ultima_mensagem_usuario[indice + len("rascunho do resumo:"):].strip()

        if "próximo campo a perguntar:" in texto_completo:
            return self._redigir_pergunta_esclarecedora(ultima_mensagem_usuario)

        # Vários agentes (ex.: EsclarecedorAgent) enviam um "contexto" rico
        # (perfil já coletado + última mensagem) em vez da mensagem crua do
        # lead. Isolamos aqui só a fala real do lead para não confundir a
        # detecção de palavras-chave com rótulos de campos como
        # "ticket_investimento" ou "expectativa_retorno".
        texto = self._isolar_fala_do_lead(ultima_mensagem_usuario).lower()

        if any(p in texto for p in _PALAVRAS_INVESTIMENTO):
            return (
                "Entendi, você está pensando em investir! Para eu te indicar as "
                "melhores oportunidades, pode me contar qual valor você pretende "
                "investir e qual retorno mensal esperado?"
            )
        if any(p in texto for p in _PALAVRAS_ALUGUEL):
            return (
                "Legal, vamos te ajudar a encontrar um imóvel para alugar! "
                "Qual região você prefere e quantos quartos você precisa?"
            )
        if any(p in texto for p in _PALAVRAS_COMPRA) or "apartamento" in texto or "casa" in texto:
            return (
                "Ótimo, vamos encontrar o imóvel ideal para compra! "
                "Você já tem uma faixa de preço em mente e prefere quantos quartos?"
            )
        return (
            f"Olá! Sou o {settings.agente_nome}, seu agente imobiliário virtual. "
            "Você está buscando comprar, alugar ou investir em um imóvel?"
        )

    @staticmethod
    def _isolar_fala_do_lead(conteudo: str) -> str:
        marcador = "última mensagem do lead:"
        indice = conteudo.lower().find(marcador)
        if indice == -1:
            return conteudo
        return conteudo[indice + len(marcador):]

    @staticmethod
    def _redigir_com_imoveis(conteudo: str) -> str:
        """Formata a lista de imóveis (montada pelo ConsultorImoveisAgent) em uma
        resposta humanizada, sem depender de um LLM de verdade."""
        partes = conteudo.split("Imóveis encontrados:")
        lista = partes[1].strip() if len(partes) > 1 else conteudo
        return (
            "Encontrei algumas opções que combinam com o que você procura:\n\n"
            f"{lista}\n\n"
            "Quer que eu agende uma visita ou uma conversa rápida com um de nossos "
            "corretores para essas opções?"
        )

    @staticmethod
    def _redigir_pergunta_esclarecedora(conteudo: str) -> str:
        """Traduz o campo já decidido pelo EsclarecedorAgent (em
        "Próximo campo a perguntar: <campo> (...)") numa pergunta pronta —
        não tenta "adivinhar" o campo de novo a partir de palavras-chave."""
        marcador = "Próximo campo a perguntar:"
        indice = conteudo.find(marcador)
        if indice == -1:
            return "Pode me contar um pouco mais sobre o que você procura?"

        linha_campo = conteudo[indice + len(marcador):].splitlines()[0].strip()
        # linha_campo é algo como "ticket_investimento (quanto o lead
        # pretende investir)" — pegamos só o nome do campo, antes do "(".
        nome_campo = linha_campo.split("(")[0].strip()

        return _PERGUNTAS_POR_CAMPO.get(
            nome_campo, "Pode me contar um pouco mais sobre o que você procura?"
        )

    @staticmethod
    def _redigir_agenda_corretor(conteudo: str) -> str:
        """Formata a agenda do corretor (montada pelo ConsultaAgendaAgent) em
        uma resposta legível, sem depender de um LLM de verdade."""
        partes = conteudo.split("Agenda do corretor:")
        lista = partes[1].strip() if len(partes) > 1 else conteudo

        if "nenhum agendamento encontrado" in lista.lower():
            return "Você não tem nenhum agendamento para esse período. 🎉"

        return f"Aqui está sua agenda:\n\n{lista}"

    def extrair_dados_estruturados(self, texto: str, schema_descricao: str) -> dict:
        """Extrai dados de uma mensagem ou, quando o texto traz o histórico da
        conversa, de TODAS as falas do lead (em ordem — a mais recente
        prevalece em caso de conflito). As falas do agente são ignoradas para
        que perguntas como "comprar, alugar ou investir?" não virem intenção."""
        dados: dict = {}
        for fala in self._falas_do_lead(texto):
            dados.update(self._extrair_de_uma_fala(fala, dados.get("intencao")))
        return dados

    @staticmethod
    def _falas_do_lead(texto: str) -> list[str]:
        marcador_ultima = "última mensagem do lead:"
        if "histórico da conversa:" not in texto.lower():
            return [texto]
        indice = texto.lower().find(marcador_ultima)
        historico = texto[:indice] if indice != -1 else texto
        ultima = texto[indice + len(marcador_ultima):].strip() if indice != -1 else ""
        falas = [
            linha.strip()[len("Lead:"):].strip()
            for linha in historico.splitlines()
            if linha.strip().startswith("Lead:")
        ]
        if ultima:
            falas.append(ultima)
        return falas

    def _extrair_de_uma_fala(self, texto: str, intencao_conhecida: str | None = None) -> dict:
        texto_lower = texto.lower()
        dados: dict = {}

        if any(p in texto_lower for p in _PALAVRAS_INVESTIMENTO):
            dados["intencao"] = "investimento"
        elif any(p in texto_lower for p in _PALAVRAS_ALUGUEL):
            dados["intencao"] = "aluguel"
        elif any(p in texto_lower for p in _PALAVRAS_COMPRA):
            dados["intencao"] = "compra"

        zona_encontrada = next((z for z in _ZONAS if z in texto_lower), None)
        bairro_encontrado = next(
            (b for b in _BAIRROS if re.search(rf"\b{re.escape(b.lower())}\b", texto_lower)), None
        )
        if zona_encontrada and bairro_encontrado:
            dados["regiao_interesse"] = f"{zona_encontrada.title()}, {bairro_encontrado}"
        elif zona_encontrada:
            dados["regiao_interesse"] = zona_encontrada.title()
        elif bairro_encontrado:
            dados["regiao_interesse"] = bairro_encontrado

        quartos = re.search(r"(\d+)\s*(quarto|dormit[oó]rio)", texto_lower)
        if quartos:
            dados["quartos_desejados"] = int(quartos.group(1))

        valores = self._parse_precos(texto_lower)
        if valores:
            # "Esse valor é orçamento ou ticket de investimento?" — decidimos
            # pela intenção desta fala ou, se ela não disser, pela intenção
            # já identificada em falas anteriores do lead (histórico).
            if (dados.get("intencao") or intencao_conhecida) == "investimento":
                dados["ticket_investimento"] = max(valores)
            else:
                dados["faixa_preco_max"] = max(valores)
                if len(valores) > 1:
                    dados["faixa_preco_min"] = min(valores)

        if "urgente" in texto_lower or "imediat" in texto_lower:
            dados["urgencia"] = "imediata"
        elif "sem pressa" in texto_lower:
            dados["urgencia"] = "sem pressa"

        if "%" in texto_lower or "retorno" in texto_lower:
            retorno = re.search(r"(\d+(?:[.,]\d+)?)\s*%", texto_lower)
            if retorno:
                dados["expectativa_retorno"] = f"{retorno.group(1)}% ao mês"

        return dados

    @staticmethod
    def _parse_precos(texto: str) -> list[float]:
        """Valores em reais: "600 mil", "5 mil", "1.200.000", "1,5 milhão", "4000"."""
        valores: list[float] = []
        for match in re.finditer(r"(\d+(?:[.,]\d+)*)\s*(milh\w*|mil\b|k\b)?", texto.lower()):
            numero_str, sufixo = match.groups()
            if not numero_str or (len(numero_str) < 2 and not sufixo):  # "5 mil" vale; "2 quartos" não
                continue
            if "," in numero_str:
                numero_str = numero_str.replace(".", "").replace(",", ".")
            elif "." in numero_str and not (sufixo and len(numero_str.split(".")[-1]) != 3):
                numero_str = numero_str.replace(".", "")  # 1.200.000 -> 1200000
            try:
                numero = float(numero_str)
            except ValueError:
                continue
            if sufixo:
                numero *= 1_000_000 if sufixo.startswith("milh") else 1000
            if numero >= 100:  # ignora números pequenos irrelevantes (ex.: "2 quartos")
                valores.append(numero)
        return valores
