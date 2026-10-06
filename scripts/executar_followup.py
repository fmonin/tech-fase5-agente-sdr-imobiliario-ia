"""Script de follow-up automático de leads inativos.

Objetivo: Reengajar automaticamente leads que iniciaram conversa mas não
responderam nos últimos N minutos, mantendo o contexto conversacional.

Uso:
    python scripts/executar_followup.py --minutos 60

Em produção, isso roda como tarefa agendada (cron job, Azure Functions,
Cloud Tasks, etc.). Aqui é um exemplo simples que pode ser executado
manualmente ou via scheduler local.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.followup_agent import FollowUpAgent  # noqa: E402
from src.config import settings  # noqa: E402
from src.infrastructure.llm.factory import criar_llm_provider  # noqa: E402
from src.infrastructure.memory.sqlite_lead_repository import SqliteLeadRepository  # noqa: E402
from src.infrastructure.observability.logger import EventoStore, configurar_logging  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Executa o follow-up automático de leads inativos.")
    parser.add_argument("--minutos", type=int, default=settings.followup_minutos,
                        help="Minutos sem resposta para considerar o lead inativo.")
    args = parser.parse_args()

    configurar_logging(settings.log_level)
    evento_store = EventoStore(settings.database_path)
    lead_repository = SqliteLeadRepository(settings.database_path)
    from src.container import montar_container

    # Mesmo agente do bot (com os argumentos de negócio para retomar pendências).
    agente_followup = montar_container().followup_agent

    # Leads com chat no Telegram recebem a mensagem lá; os da web veem no
    # histórico quando voltarem para a página.
    notifier = sessoes = None
    if settings.telegram_habilitado:
        from src.infrastructure.notifications.telegram_notifier import TelegramNotifier
        from src.infrastructure.notifications.telegram_sessoes import SessoesTelegram

        notifier, sessoes = TelegramNotifier(), SessoesTelegram()

    enviados = agente_followup.executar_para_leads_inativos(args.minutos)
    for enviado in enviados:
        chats = sessoes.chats_do_lead(enviado.lead.id) if sessoes else []
        for chat_id in chats:
            notifier.enviar(chat_id, enviado.mensagem)
        evento_store.registrar_evento("followup_disparado", {"lead_id": enviado.lead.id, "telegram": bool(chats)})
        print(f"Follow-up para {enviado.lead.nome or enviado.lead.id}: {enviado.mensagem}")

    print(f"Total de leads reengajados: {len(enviados)}")


if __name__ == "__main__":
    main()
