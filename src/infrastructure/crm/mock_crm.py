"""Integração simulada com um CRM.

Implementa `ICRM`. Em uma imobiliária real, isso chamaria a API de um CRM
real (RD Station, Pipedrive, Salesforce, etc.). Aqui, para manter a aplicação
simples e sem depender de credenciais externas, gravamos os registros em um
arquivo JSON local — simulando o efeito de sincronizar com o CRM.

Extensibilidade:
    Trocar por uma integração real é implementar `ICRM` em uma nova classe
    (ex.: `PipedriveCRM`) — Open/Closed Principle. O resto do sistema
    não precisa mudar.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

from src.domain.entities import Agendamento, Lead, ResumoCorretor
from src.domain.interfaces import ICRM

_log = logging.getLogger(__name__)
_VAZIO = {"leads": [], "resumos": [], "agendamentos": []}


class MockCRM(ICRM):
    def __init__(self, caminho_arquivo: str | Path = "data/crm_simulado.json") -> None:
        self._caminho = Path(caminho_arquivo)
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        if not self._caminho.exists():
            self._salvar(dict(_VAZIO))

    def _carregar(self) -> dict:
        try:
            with open(self._caminho, encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Arquivo corrompido (ex.: processo interrompido no meio da gravação).
            # O CRM é só um registro simulado: guarda a cópia e recomeça, sem
            # derrubar o atendimento.
            copia = self._caminho.with_suffix(".corrompido.json")
            os.replace(self._caminho, copia)
            _log.warning("CRM simulado corrompido; cópia salva em %s e arquivo recriado.", copia)
            dados = dict(_VAZIO)
            self._salvar(dados)
        for chave, vazio in _VAZIO.items():
            dados.setdefault(chave, list(vazio))
        return dados

    def _salvar(self, dados: dict) -> None:
        # Grava num temporário e troca de uma vez: o arquivo nunca fica pela metade.
        temporario = self._caminho.with_suffix(".tmp")
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False, indent=2, default=str)
        os.replace(temporario, self._caminho)

    def registrar_lead(self, lead: Lead) -> None:
        dados = self._carregar()
        registro = {"id": lead.id, "nome": lead.nome, "canal": lead.canal, "intencao": lead.perfil.intencao.value}
        dados["leads"] = [r for r in dados["leads"] if r["id"] != lead.id] + [registro]
        self._salvar(dados)

    def registrar_resumo(self, resumo: ResumoCorretor) -> None:
        dados = self._carregar()
        dados["resumos"].append(asdict(resumo))
        self._salvar(dados)

    def registrar_agendamento(self, agendamento: Agendamento) -> None:
        dados = self._carregar()
        dados["agendamentos"].append(asdict(agendamento))
        self._salvar(dados)
