"""Captações do corretor: imóveis de clientes (venda/locação) com avaliação
agendada pelo Sr. Agim (`CaptacaoImovelAgent`).

O corretor vê a ficha de cada um (tipo, quartos, endereço, estimativa
preliminar) e, depois da avaliação, registra o resultado:
    • 🏷️ Captado  -> informa o valor de anúncio e o imóvel entra na base
                     (com o match automático de clientes interessados);
    • 🤔 Proprietário vai pensar;
    • ❌ Não captado (motivo).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.domain.entities import Agendamento, Corretor, Imovel, Lead

RESULTADOS_CAPTACAO = {
    "captado": "🏷️ Captado — publicar na base",
    "pensando": "🤔 Proprietário vai pensar",
    "nao_captado": "❌ Não captado",
}


@dataclass
class ItemCaptacao:
    agendamento: Agendamento
    lead: Optional[Lead]
    dados: dict  # cadastro feito no chat (tipo, quartos, endereço, estimativa...)

    @property
    def finalidade(self) -> str:
        return "venda" if self.dados.get("finalidade", "venda") == "venda" else "locação"


class CaptacaoService:
    def __init__(self, agenda_repository, lead_repository, match_imoveis, observador=None) -> None:
        self._agenda = agenda_repository
        self._leads = lead_repository
        self._match = match_imoveis
        self._observador = observador

    def listar(self, corretor_id: str, incluir_finalizadas: bool = False) -> list[ItemCaptacao]:
        itens = []
        for a in self._agenda.listar_por_corretor(corretor_id):
            if a.tipo != "avaliacao" or a.status == "cancelado":
                continue
            if not incluir_finalizadas and a.resultado_visita in ("captado", "nao_captado"):
                continue
            lead = self._leads.buscar_por_id(a.lead_id) if a.lead_id else None
            dados = next((c for c in (lead.captacoes if lead else []) if c.get("agendamento_id") == a.id), {})
            itens.append(ItemCaptacao(a, lead, dados))
        return sorted(itens, key=lambda i: i.agendamento.data_hora or datetime.max)

    def registrar_resultado(
        self, agendamento_id: str, corretor: Corretor, resultado: str,
        valor: Optional[float] = None, motivo: Optional[str] = None,
    ) -> tuple[bool, str, Optional[Imovel], int]:
        """(ok, mensagem, imóvel publicado, nº de clientes compatíveis)."""
        if resultado not in RESULTADOS_CAPTACAO:
            return False, "Resultado inválido.", None, 0
        item = next((i for i in self.listar(corretor.id, incluir_finalizadas=True)
                     if i.agendamento.id == agendamento_id), None)
        if item is None:
            return False, "Captação não encontrada na sua agenda.", None, 0
        if resultado == "captado" and not valor:
            return False, "Informe o valor de anúncio para publicar o imóvel.", None, 0
        a = item.agendamento
        a.resultado_visita = resultado
        a.motivo_resultado = ((motivo or "").strip() or None) if resultado == "nao_captado" else None
        a.valor_negociado = float(valor) if valor else a.valor_negociado
        a.resultado_em = datetime.utcnow()

        imovel, compativeis = None, 0
        if resultado == "captado":
            publicado = self._publicar(item, float(valor), corretor)
            imovel, compativeis = publicado.imovel, len(publicado.clientes)
            a.imovel_id = imovel.id
        self._agenda.registrar(a)
        if self._observador:
            self._observador.registrar_evento("captacao_resultado", {
                "agendamento_id": a.id, "corretor_id": corretor.id, "resultado": resultado,
                "imovel_id": imovel.id if imovel else None})

        texto = f"Resultado da captação: {RESULTADOS_CAPTACAO[resultado]}"
        if imovel:
            texto = (f"🏷️ Imóvel captado e publicado como {imovel.id} ({imovel.titulo}). "
                     f"{compativeis} cliente(s) procuram algo assim — veja a aba 🏠 Imóveis novos para avisá-los.")
        elif a.motivo_resultado:
            texto += f" (motivo: {a.motivo_resultado})"
        return True, texto, imovel, compativeis

    def _publicar(self, item: ItemCaptacao, valor: float, corretor: Corretor):
        """Cadastra na base e já devolve os clientes compatíveis (ImovelComMatch)."""
        d = item.dados
        tipo = d.get("tipo") or "Apartamento"
        quartos = int(d.get("quartos") or 0)
        bairro = d.get("bairro") or ""
        negocio = "venda" if d.get("finalidade", "venda") == "venda" else "aluguel"
        titulo = f"{tipo}{f' {quartos} quarto(s)' if quartos else ''} em {bairro} (captação)"
        descricao = (item.agendamento.detalhes or titulo).splitlines()[0]
        return self._match.cadastrar(Imovel(
            id="", titulo=titulo, tipo_negocio=negocio, finalidade_investimento=False, zona=d.get("zona") or "",
            bairro=bairro, preco=valor, quartos=quartos, metragem=float(d.get("metragem") or 0) or 50.0,
            descricao=descricao, tipo_imovel=tipo, suites=0, vagas=int(d.get("vagas") or 0),
        ), corretor)
