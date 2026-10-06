"""Repositório de leads com persistência em SQLite.

Implementa `ILeadRepository`. É o que dá "memória conversacional" ao
agente: o histórico de mensagens e o perfil do lead sobrevivem entre
execuções (fechar e abrir o Streamlit de novo, reiniciar o processo etc.),
porque tudo fica salvo em um arquivo .db local (SQLite não precisa de
servidor, ótimo para uma POC).

Guardamos o Lead inteiro serializado em JSON numa única coluna. Isso é uma
simplificação proposital para manter o projeto simples para quem está
aprendendo — em um sistema maior, cada campo viraria uma coluna/tabela
normalizada.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4

from src.domain.entities import (
    Agendamento,
    IntencaoLead,
    Lead,
    Mensagem,
    PerfilLead,
    RemetenteMensagem,
    TemperaturaLead,
)
from src.domain.interfaces import ILeadRepository

_CRIAR_TABELA = """
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    dados_json TEXT NOT NULL,
    ultima_interacao_em TEXT NOT NULL
)
"""


class SqliteLeadRepository(ILeadRepository):
    def __init__(self, caminho_db: str | Path = "data/agente_sdr.db") -> None:
        self._caminho = Path(caminho_db)
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as conexao:
            conexao.execute(_CRIAR_TABELA)

    def _conectar(self) -> sqlite3.Connection:
        return sqlite3.connect(self._caminho)

    def salvar(self, lead: Lead) -> None:
        dados_json = json.dumps(self._lead_para_dict(lead), ensure_ascii=False)
        with self._conectar() as conexao:
            conexao.execute(
                "INSERT INTO leads (id, dados_json, ultima_interacao_em) VALUES (?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET dados_json = excluded.dados_json, "
                "ultima_interacao_em = excluded.ultima_interacao_em",
                (lead.id, dados_json, lead.ultima_interacao_em.isoformat()),
            )

    def buscar_por_id(self, lead_id: str) -> Optional[Lead]:
        with self._conectar() as conexao:
            linha = conexao.execute(
                "SELECT dados_json FROM leads WHERE id = ?", (lead_id,)
            ).fetchone()
        if not linha:
            return None
        return self._dict_para_lead(json.loads(linha[0]))

    def buscar_por_cpf(self, cpf: str) -> Optional[Lead]:
        # Não há uma coluna indexada para CPF (o Lead inteiro fica em JSON
        # numa única coluna — veja o docstring do módulo). Para o volume de
        # dados de uma POC, percorrer os leads em Python é simples e rápido
        # o suficiente; num sistema com muito mais leads, valeria a pena
        # extrair o CPF para uma coluna própria e indexada.
        for lead in self.listar_todos():
            if lead.cpf == cpf:
                return lead
        return None

    def listar_todos(self) -> list[Lead]:
        with self._conectar() as conexao:
            linhas = conexao.execute(
                "SELECT dados_json FROM leads ORDER BY ultima_interacao_em DESC"
            ).fetchall()
        return [self._dict_para_lead(json.loads(linha[0])) for linha in linhas]

    def listar_aguardando_followup(self, minutos_inatividade: int) -> list[Lead]:
        limite = datetime.utcnow() - timedelta(minutes=minutos_inatividade)
        return [
            lead
            for lead in self.listar_todos()
            if lead.aguardando_resposta_desde is not None
            and lead.aguardando_resposta_desde <= limite
        ]

    # -- (de)serialização ---------------------------------------------------
    @staticmethod
    def _lead_para_dict(lead: Lead) -> dict:
        dados = asdict(lead)
        dados["ultima_interacao_em"] = lead.ultima_interacao_em.isoformat()
        dados["aguardando_resposta_desde"] = (
            lead.aguardando_resposta_desde.isoformat() if lead.aguardando_resposta_desde else None
        )
        for i, mensagem in enumerate(lead.historico):
            dados["historico"][i]["criado_em"] = mensagem.criado_em.isoformat()
        for i, agendamento in enumerate(lead.agendamentos):
            dados["agendamentos"][i]["criado_em"] = agendamento.criado_em.isoformat()
            dados["agendamentos"][i]["data_hora"] = (
                agendamento.data_hora.isoformat() if agendamento.data_hora else None
            )
            dados["agendamentos"][i]["resultado_em"] = (
                agendamento.resultado_em.isoformat() if agendamento.resultado_em else None
            )
            dados["agendamentos"][i]["cancelado_em"] = (
                agendamento.cancelado_em.isoformat() if agendamento.cancelado_em else None
            )
        return dados

    @staticmethod
    def _dict_para_lead(dados: dict) -> Lead:
        perfil_dados = dados["perfil"]
        perfil = PerfilLead(
            intencao=IntencaoLead(perfil_dados["intencao"]),
            faixa_preco_min=perfil_dados.get("faixa_preco_min"),
            faixa_preco_max=perfil_dados.get("faixa_preco_max"),
            quartos_desejados=perfil_dados.get("quartos_desejados"),
            regiao_interesse=perfil_dados.get("regiao_interesse"),
            urgencia=perfil_dados.get("urgencia"),
            ticket_investimento=perfil_dados.get("ticket_investimento"),
            expectativa_retorno=perfil_dados.get("expectativa_retorno"),
            temperatura=TemperaturaLead(perfil_dados.get("temperatura", "frio")),
            imovel_interesse_id=perfil_dados.get("imovel_interesse_id"),
        )
        historico = [
            Mensagem(
                remetente=RemetenteMensagem(m["remetente"]),
                conteudo=m["conteudo"],
                criado_em=datetime.fromisoformat(m["criado_em"]),
            )
            for m in dados.get("historico", [])
        ]
        agendamentos = [
            Agendamento(
                id=a.get("id", str(uuid4())),
                lead_id=a["lead_id"],
                quando_sugerido=a["quando_sugerido"],
                tipo=a.get("tipo", "reuniao"),
                status=a.get("status", "confirmado" if a.get("confirmado") else "sugerido"),
                confirmado=a.get("confirmado", False),
                data_hora=datetime.fromisoformat(a["data_hora"]) if a.get("data_hora") else None,
                cliente_cpf=a.get("cliente_cpf"),
                cliente_nome=a.get("cliente_nome"),
                corretor_id=a.get("corretor_id"),
                corretor_nome=a.get("corretor_nome"),
                imovel_id=a.get("imovel_id"),
                imovel_titulo=a.get("imovel_titulo"),
                criado_em=datetime.fromisoformat(a["criado_em"]),
                cancelado_por=a.get("cancelado_por"),
                motivo_cancelamento=a.get("motivo_cancelamento"),
                cancelado_em=datetime.fromisoformat(a["cancelado_em"]) if a.get("cancelado_em") else None,
                cliente_notificado=a.get("cliente_notificado", False),
                horario_anterior=a.get("horario_anterior"),
                motivo_alteracao=a.get("motivo_alteracao"),
            )
            for a in dados.get("agendamentos", [])
        ]
        return Lead(
            id=dados["id"],
            nome=dados.get("nome"),
            cpf=dados.get("cpf"),
            canal=dados.get("canal", "web"),
            perfil=perfil,
            historico=historico,
            agendamentos=agendamentos,
            ultima_interacao_em=datetime.fromisoformat(dados["ultima_interacao_em"]),
            aguardando_resposta_desde=(
                datetime.fromisoformat(dados["aguardando_resposta_desde"])
                if dados.get("aguardando_resposta_desde")
                else None
            ),
            followups_enviados=dados.get("followups_enviados", 0),
            resumo_corretor=dados.get("resumo_corretor"),
            buscas_anteriores=dados.get("buscas_anteriores", []),
            feedback_visitas=dados.get("feedback_visitas", []),
            imoveis_avisados=dados.get("imoveis_avisados", []),
            captacao=dados.get("captacao", {}),
            captacoes=dados.get("captacoes", []),
            atendimento_encerrado=dados.get("atendimento_encerrado", False),
            notas_atendimento=dados.get("notas_atendimento", []),
            nao_contatar=dados.get("nao_contatar", False),
        )
