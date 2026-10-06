"""Configuração central da aplicação.

Onde fica cada coisa (boa prática "12-factor" + segredos fora do Git):

    config/settings.toml        -> configurações do projeto (VAI para o Git):
                                   provedor de LLM, endpoint e deployment do
                                   Azure, tempos do follow-up, caminhos...
    config/settings.local.toml  -> ajustes SÓ desta máquina (fora do Git),
                                   ex.: tempos curtos para a demonstração.
    .env                        -> SOMENTE segredos (chaves e tokens) — fora
                                   do Git. Modelo: .env.example.

Precedência (o último vence):
    padrão no código < settings.toml < settings.local.toml < variáveis de ambiente/.env

Assim, em produção (Docker, Azure) qualquer valor pode ser sobrescrito por
variável de ambiente, sem editar arquivos.

Clean Architecture / SOLID:
    • `Settings` é o CONTRATO tipado e imutável que o resto do app usa
      (`from src.config import settings`) — ninguém mais lê arquivo ou
      `os.getenv`.
    • As FONTES (TOML, ambiente) são adaptadores em
      `src/infrastructure/configuracao.py` (Single Responsibility); trocar
      ou acrescentar uma fonte (ex.: Azure Key Vault) não muda `Settings`
      (Open/Closed + Dependency Inversion).
    • Cada campo declara de onde vem (chave no TOML e nome da variável de
      ambiente) e se é SEGREDO. Segredo em arquivo de configuração é
      recusado na inicialização — nunca vai parar no Git por engano.
"""
from __future__ import annotations

import logging
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from dotenv import load_dotenv

from src.infrastructure.configuracao import AmbienteFonte, ArquivoTomlFonte, FonteConfiguracao

RAIZ_PROJETO = Path(__file__).resolve().parents[1]
_log = logging.getLogger(__name__)


def _campo(chave: str, env: str, padrao: Any, segredo: bool = False):
    """Declara uma configuração: chave no TOML ("secao.nome"), variável de
    ambiente que a sobrescreve, valor padrão e se é segredo."""
    return field(default=padrao, repr=not segredo, metadata={"chave": chave, "env": env, "segredo": segredo})


@dataclass(frozen=True)
class Settings:
    """Todas as configurações do projeto (imutável depois de criado)."""

    # Aplicação
    app_env: str = _campo("app.ambiente", "APP_ENV", "development")
    log_level: str = _campo("app.log_level", "LOG_LEVEL", "INFO")
    agente_nome: str = _campo("agente.nome", "AGENTE_NOME", "Sr. Agim")

    # LLM: "auto" (Azure se houver chave, senão Mock), "azure" ou "mock"
    llm_provider: str = _campo("llm.provider", "LLM_PROVIDER", "auto")

    # Azure OpenAI
    azure_openai_api_key: str = _campo("azure_openai.api_key", "AZURE_OPENAI_API_KEY", "", segredo=True)
    azure_openai_endpoint: str = _campo("azure_openai.endpoint", "AZURE_OPENAI_ENDPOINT", "")
    azure_openai_api_version: str = _campo("azure_openai.api_version", "AZURE_OPENAI_API_VERSION", "2024-10-21")
    azure_openai_deployment: str = _campo("azure_openai.deployment", "AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1-mini")
    azure_openai_embedding_deployment: str = _campo(
        "azure_openai.embedding_deployment", "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")

    # Embeddings do RAG: "local" (sentence-transformers), "azure" ou "tfidf"
    embeddings_provider: str = _campo("embeddings.provider", "EMBEDDINGS_PROVIDER", "local")
    embeddings_modelo_local: str = _campo(
        "embeddings.modelo_local", "EMBEDDINGS_MODELO_LOCAL",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    embeddings_cache_path: str = _campo("embeddings.cache_path", "EMBEDDINGS_CACHE_PATH", "data/embeddings_cache.json")

    # Azure Speech (Voice AI, opcional)
    azure_speech_key: str = _campo("azure_speech.api_key", "AZURE_SPEECH_KEY", "", segredo=True)
    azure_speech_region: str = _campo("azure_speech.region", "AZURE_SPEECH_REGION", "brazilsouth")
    azure_speech_voz: str = _campo("azure_speech.voz", "AZURE_SPEECH_VOZ", "pt-BR-AntonioNeural")
    voz_responder_em_audio: bool = _campo("azure_speech.responder_em_audio", "VOZ_RESPONDER_EM_AUDIO", True)

    # Telegram
    telegram_bot_token: str = _campo("telegram.bot_token", "TELEGRAM_BOT_TOKEN", "", segredo=True)

    # Follow-up automático e pós-visita
    followup_minutos: int = _campo("followup.minutos_sem_resposta", "FOLLOWUP_MINUTOS", 60)
    followup_intervalo_minutos: int = _campo("followup.intervalo_verificacao_minutos", "FOLLOWUP_INTERVALO_MINUTOS", 5)
    pos_visita_horas: float = _campo("pos_visita.horas_apos_visita", "POS_VISITA_HORAS", 2.0)

    # Dados (caminhos relativos à pasta do projeto)
    database_path: str = _campo("dados.database_path", "DATABASE_PATH", "data/agente_sdr.db")
    imoveis_database_path: str = _campo("dados.imoveis_database_path", "IMOVEIS_DATABASE_PATH", "data/imoveis.db")
    imoveis_seed_json_path: str = _campo("dados.imoveis_seed_json_path", "IMOVEIS_SEED_JSON_PATH", "data/imoveis.json")
    bairros_json_path: str = _campo("dados.bairros_json_path", "BAIRROS_JSON_PATH", "data/bairros_sp.json")
    mercado_json_path: str = _campo("dados.mercado_json_path", "MERCADO_JSON_PATH", "data/mercado_investimento.json")

    def __post_init__(self) -> None:
        # Normaliza textos que são "opções" (evita "Azure" ≠ "azure").
        for nome in ("llm_provider", "embeddings_provider"):
            object.__setattr__(self, nome, str(getattr(self, nome)).strip().lower())

    @property
    def usando_llm_real(self) -> bool:
        """True quando o projeto vai chamar o Azure OpenAI de verdade."""
        if self.llm_provider == "mock" or not self.azure_openai_api_key:
            return False
        return self.llm_provider == "azure" or (self.llm_provider == "auto" and bool(self.azure_openai_endpoint))

    @property
    def descricao_llm(self) -> str:
        """Qual LLM está ativo e POR QUÊ — mostrado no terminal e na tela."""
        if self.usando_llm_real:
            recurso = self.azure_openai_endpoint.split("//")[-1].split(".")[0] or "?"
            return f"Azure OpenAI — modelo {self.azure_openai_deployment} (recurso {recurso})"
        if self.llm_provider == "mock":
            motivo = 'llm.provider = "mock" na configuração'
        elif not self.azure_openai_api_key:
            motivo = "AZURE_OPENAI_API_KEY vazia ou ausente no .env"
        else:
            motivo = "azure_openai.endpoint não configurado em config/settings.toml"
        return f"Mock (LLM simulado, sem custo) — motivo: {motivo}"

    @property
    def telegram_habilitado(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def voice_habilitado(self) -> bool:
        return bool(self.azure_speech_key)


# --------------------------------------------------------------------- carga
def _converter(valor: Any, padrao: Any) -> Any:
    tipo = type(padrao)
    if tipo is bool:
        return valor if isinstance(valor, bool) else str(valor).strip().lower() in {"1", "true", "yes", "sim"}
    if tipo in (int, float):
        return tipo(valor)
    return str(valor)


def fontes_padrao(raiz: Path = RAIZ_PROJETO, ambiente: Optional[Mapping[str, str]] = None) -> list[FonteConfiguracao]:
    nomes_env = {f.metadata["chave"]: f.metadata["env"] for f in fields(Settings)}
    return [
        ArquivoTomlFonte(raiz / "config" / "settings.toml"),
        ArquivoTomlFonte(raiz / "config" / "settings.local.toml"),
        AmbienteFonte(nomes_env, ambiente),
    ]


def carregar_settings(fontes: Optional[Iterable[FonteConfiguracao]] = None) -> Settings:
    """Monta o `Settings` aplicando as fontes em ordem (a última vence)."""
    campos = {f.metadata["chave"]: f for f in fields(Settings)}
    valores: dict[str, Any] = {}
    for fonte in fontes if fontes is not None else fontes_padrao():
        dados = fonte.ler()
        eh_arquivo = isinstance(fonte, ArquivoTomlFonte)
        for chave, valor in dados.items():
            campo = campos.get(chave)
            if campo is None:
                _log.warning("Configuração desconhecida '%s' em %s (ignorada).", chave, fonte.nome)
                continue
            if eh_arquivo and campo.metadata["segredo"]:
                raise ValueError(
                    f"'{chave}' é um SEGREDO e não pode ficar em {fonte.nome} (arquivo que pode ir para o Git). "
                    f"Coloque-o no .env como {campo.metadata['env']}=..."
                )
            padrao = campo.default if campo.default is not MISSING else ""
            valores[campo.name] = _converter(valor, padrao)
    return Settings(**valores)


# O .env (só segredos) entra nas variáveis de ambiente do processo. Variáveis
# já definidas pelo ambiente (Docker, Azure, testes) têm prioridade.
load_dotenv(RAIZ_PROJETO / ".env")

# Instância única importada pelo resto do app:  from src.config import settings
settings = carregar_settings()
