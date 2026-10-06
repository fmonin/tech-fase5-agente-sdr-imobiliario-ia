"""Interfaces (portas) do domínio.

Este é o coração da arquitetura SOLID do projeto:

- **D**ependency Inversion Principle: os agentes (camada de cima) e a
  infraestrutura (camada de baixo) dependem destas *interfaces abstratas*,
  nunca uma da outra diretamente. Por exemplo, os agentes usam
  `ILLMProvider`, e não `AzureOpenAIProvider` diretamente. Isso permite
  trocar Azure OpenAI por outro provedor (ou por um "mock" nos testes) sem
  alterar uma linha sequer dos agentes.
- **I**nterface Segregation Principle: cada interface tem poucos métodos,
  focados em uma única responsabilidade (buscar imóveis, enviar mensagem,
  gerar fala, etc.), em vez de uma única interface gigante "faz tudo".
- **O**pen/Closed Principle: para adicionar um novo provedor de LLM ou um
  novo canal de notificação, basta criar uma nova classe que implemente a
  interface — não é necessário modificar código existente.

Para um estudante iniciante:
    Uma "interface" em Python normalmente é escrita com `abc.ABC` +
    `@abstractmethod`. Ela funciona como um "contrato": qualquer classe que
    implementar `ILLMProvider` É OBRIGADA a ter um método `gerar_resposta`.
    Isso é o que possibilita, por exemplo, o `MockLLMProvider` e o
    `AzureOpenAIProvider` serem intercambiáveis (polimorfismo).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Protocol

from src.domain.entities import Agendamento, Corretor, Imovel, Lead, ResumoCorretor
from src.domain.investimento import IndicadoresMercado


class ILLMProvider(ABC):
    """Contrato para qualquer provedor de modelo de linguagem (LLM)."""

    @abstractmethod
    def gerar_resposta(self, mensagens: list[dict], temperatura: float = 0.4) -> str:
        """Recebe mensagens no formato [{"role": "system"|"user"|"assistant", "content": str}]
        e retorna o texto gerado pelo modelo."""
        raise NotImplementedError

    @abstractmethod
    def extrair_dados_estruturados(self, texto: str, schema_descricao: str) -> dict:
        """Usa o LLM para extrair informações estruturadas (ex.: intenção,
        orçamento) a partir de uma mensagem em linguagem natural."""
        raise NotImplementedError


class IPropertyRepository(ABC):
    """Contrato para acessar a base (simulada) de imóveis."""

    @abstractmethod
    def listar_todos(self) -> list[Imovel]:
        raise NotImplementedError

    @abstractmethod
    def buscar(
        self,
        tipo_negocio: Optional[str] = None,
        zona: Optional[str] = None,
        preco_max: Optional[float] = None,
        preco_min: Optional[float] = None,
        quartos_min: Optional[int] = None,
    ) -> list[Imovel]:
        raise NotImplementedError

    def adicionar(self, imovel: Imovel) -> Imovel:
        """Cadastra um imóvel novo (opcional: nem toda base aceita escrita)."""
        raise NotImplementedError("Esta base de imóveis é somente leitura.")


class IVectorSearch(ABC):
    """Contrato para a busca semântica (RAG) sobre os imóveis."""

    @abstractmethod
    def buscar_similares(self, consulta: str, top_k: int = 3) -> list[Imovel]:
        raise NotImplementedError


class ILeadRepository(ABC):
    """Contrato para persistência dos leads (memória conversacional)."""

    @abstractmethod
    def salvar(self, lead: Lead) -> None:
        raise NotImplementedError

    @abstractmethod
    def buscar_por_id(self, lead_id: str) -> Optional[Lead]:
        raise NotImplementedError

    @abstractmethod
    def buscar_por_cpf(self, cpf: str) -> Optional[Lead]:
        """Recupera o lead (e todo o histórico de conversa) de um cliente que
        já falou com a imobiliária antes, identificado pelo CPF."""
        raise NotImplementedError

    @abstractmethod
    def listar_todos(self) -> list[Lead]:
        raise NotImplementedError

    @abstractmethod
    def listar_aguardando_followup(self, minutos_inatividade: int) -> list[Lead]:
        raise NotImplementedError


class ICorretorRepository(ABC):
    """Contrato para o cadastro de corretores (cada um com sua área de atuação)."""

    @abstractmethod
    def listar_todos(self) -> list[Corretor]:
        raise NotImplementedError

    @abstractmethod
    def buscar_por_id(self, corretor_id: str) -> Optional[Corretor]:
        raise NotImplementedError

    @abstractmethod
    def buscar_por_zona(self, zona: str) -> list[Corretor]:
        """Retorna os corretores cuja área de atuação cobre a zona informada."""
        raise NotImplementedError


class IAgendaRepository(ABC):
    """Contrato para a base de agenda — consultável tanto pelo cliente
    (pelo CPF) quanto pelo corretor (pelo id do corretor)."""

    @abstractmethod
    def registrar(self, agendamento: Agendamento) -> None:
        raise NotImplementedError

    @abstractmethod
    def listar_por_cliente(self, cpf: str) -> list[Agendamento]:
        raise NotImplementedError

    @abstractmethod
    def listar_por_corretor(self, corretor_id: str) -> list[Agendamento]:
        raise NotImplementedError

    @abstractmethod
    def buscar_por_id(self, agendamento_id: str) -> Optional[Agendamento]:
        ...

    @abstractmethod
    def listar_avisos_pendentes(self, cpf: str) -> list[Agendamento]:
        """Agendamentos do cliente que o corretor cancelou ou remarcou e que
        ainda não foram comunicados a ele."""
        ...

    @abstractmethod
    def listar_avisos_corretor(self, corretor_id: str) -> list[Agendamento]:
        """Agendamentos que o CLIENTE cancelou ou remarcou e que o corretor
        ainda não viu (aviso na "Área do Corretor")."""
        return []

    def contar_agendamentos_ativos(self, corretor_id: str) -> int:
        """Usado pelo AgendadorAgent para balancear a atribuição automática
        entre corretores que cobrem a mesma zona (o menos ocupado leva)."""
        raise NotImplementedError


class IMarketDataRepository(ABC):
    """Fonte dos indicadores de mercado (Selic, CDI, IPCA, FipeZAP)."""

    @abstractmethod
    def obter_indicadores(self) -> IndicadoresMercado:
        ...


class INotifier(ABC):
    """Contrato para envio de notificações (ex.: Telegram, e-mail, CRM)."""

    @abstractmethod
    def enviar(self, destinatario: str, mensagem: str) -> None:
        raise NotImplementedError


class ICRM(ABC):
    """Contrato para integração com um CRM (aqui, simulado em arquivo)."""

    @abstractmethod
    def registrar_lead(self, lead: Lead) -> None:
        raise NotImplementedError

    @abstractmethod
    def registrar_resumo(self, resumo: ResumoCorretor) -> None:
        raise NotImplementedError

    @abstractmethod
    def registrar_agendamento(self, agendamento: Agendamento) -> None:
        raise NotImplementedError


class IVoiceService(ABC):
    """Contrato para conversão texto->fala e fala->texto (Voice AI)."""

    @abstractmethod
    def texto_para_fala(self, texto: str, formato: str = "ogg-48khz-16bit-mono-opus") -> bytes:
        raise NotImplementedError

    @abstractmethod
    def fala_para_texto(self, audio_bytes: bytes, formato: str = "ogg") -> str:
        """`formato`: "ogg" (Opus, notas de voz do Telegram) ou "wav" (microfone do navegador)."""
        raise NotImplementedError


class IObservador(Protocol):
    """Protocolo (interface estrutural) para observabilidade.

    Usamos `Protocol` aqui só para mostrar a alternativa ao `ABC`: qualquer
    objeto com um método `registrar_evento(nome, dados)` serve, sem precisar
    herdar explicitamente. Isso também é Dependency Inversion.
    """

    def registrar_evento(self, nome: str, dados: dict) -> None: ...
