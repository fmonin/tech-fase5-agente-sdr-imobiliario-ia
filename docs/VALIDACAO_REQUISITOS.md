# Validação dos requisitos — Sr. Agim (Agente SDR Imobiliário)

Validação feita em 03/10/2026 contra o "Objetivo do Desafio" e os "Cenários
Esperados" do enunciado do Tech Challenge — Fase 5. Cada item foi conferido no
código e coberto por testes automatizados (`pytest`, **240 testes passando**).
Os testes rodam com o `MockLLMProvider` (sem custo e sem rede); com
`LLM_PROVIDER=azure` o mesmo fluxo usa o Azure OpenAI (`gpt-4.1-mini`).

## Objetivos do desafio

| # | Requisito | Status | Onde está | Evidência (testes) |
|---|---|---|---|---|
| 1 | Atender leads automaticamente | ✅ | `ConversationService` + grafo LangGraph; canais web (Streamlit) e Telegram (`telegram_bot.py`), 24h, sem intervenção humana | `test_graph.py`, `test_telegram_bot.py` |
| 2 | Conversa humanizada | ✅ | Agente com nome (Sr. Agim), saudação, uso do primeiro nome, cliente recorrente reconhecido pelo CPF ("Que bom te ver de novo"), memória da conversa, respostas curtas e naturais redigidas pelo LLM | `test_memoria_conversa.py`, `test_identification_agent.py` |
| 3 | Qualificar clientes | ✅ | `QualificadorAgent`: extração do perfil + temperatura (frio/morno/quente); `EsclarecedorAgent` decide em código o que falta perguntar | `test_qualifier_agent.py`, `test_cenarios_desafio.py` |
| 4 | Identificar intenção (compra, aluguel, investimento) | ✅ | Extração pelo LLM + regras; se a intenção for ambígua ("procurando apartamento"), o agente pergunta "comprar ou alugar?"; troca de intenção no meio da conversa recomeça o perfil; **além do desafio**: reconhece também o PROPRIETÁRIO que quer vender ou colocar para alugar o próprio imóvel ("quero vender meu apê") e abre o fluxo de captação (cadastro, estimativa de valor, avaliação agendada com o corretor — `CaptacaoImovelAgent`) | `test_cenarios_desafio.py::test_exemplo_1_compra`, `test_nova_busca.py`, `test_captacao.py` |
| 5 | Coletar informações relevantes | ✅ | Compra/aluguel: região, quartos, orçamento, **urgência**; investimento: ticket e expectativa de retorno; nome e CPF no cadastro | `test_cenarios_desafio.py`, `test_cpf.py` |
| 6 | Follow-up automático | ✅ | `FollowUpAgent`: retoma o contato com o contexto (nome, interesse, ponto em que parou); roda **sozinho no bot do Telegram** (1ª verificação ~20 s após iniciar e depois a cada `followup.intervalo_verificacao_minutos` de `config/settings.toml`, com log no terminal e evento `followup_disparado`), comando `/followup` para demonstração ao vivo e também via `scripts/executar_followup.py`; no máx. 2 tentativas seguidas (3 quando há **negócio pela metade** — compra, aluguel, investimento ou venda do próprio imóvel —, mesmo se o cliente encerrou a conversa, com argumentos reais calculados da base; quem pede para parar não recebe mais); **pós-visita automático** ("Como foi a visita?") com o retorno repassado ao corretor; só para conversas **sem agendamento** (não envia a quem tem visita/reunião confirmada ou remarcada; proposta não aceita ou cancelamento ainda recebem) | `test_followup.py` |
| 7 | Agendar reuniões ou visitas | ✅ | `AgendadorAgent`: propõe horário, entende "dia 8 às 13h", "sexta de manhã", troca de horário e confirmação; corretor atribuído pela zona do imóvel; investidor vai para o especialista; agenda consultável/cancelável/remarcável pelo corretor; ao se identificar (CPF ou /start no Telegram) o cliente vê todas as agendas ativas com o(a) corretor(a) e telefone e pode **cancelar ou remarcar** as próprias agendas pelo chat (`AgendaClienteAgent`), com aviso automático ao corretor na Área do Corretor | `test_agendas_do_cliente.py`, `test_cliente_gerencia_agenda.py`, `test_aceite_agendamento.py`, `test_cliente_muda_horario.py`, `test_gestao_agenda_corretor.py` |
| 8 | Integrar com base simulada de imóveis | ✅ | 41 imóveis em `data/imoveis.json` → SQLite (consultas SQL), fotos, busca por proximidade de bairro, RAG com embeddings, análise de investimento com dados de mercado (FipeZAP/Selic/CDI) | `test_property_repository.py`, `test_sqlite_property_repository.py`, `test_proximidade_e_embeddings.py`, `test_fotos_imoveis.py`, `test_investimento.py` |
| 9 | Gerar resumos para corretores | ✅ | `ResumidorAgent` gera o resumo **quando a visita/reunião é confirmada** (ou o lead fica quente), salvo no lead e no CRM; visível no **Painel da Imobiliária** e em cada agendamento da **Área do Corretor** (que também lista os clientes da área sem visita e sugere imóveis extras para os clientes agendados — `CarteiraCorretorService`), com botão "Gerar/Atualizar resumo com IA" | `test_cenarios_desafio.py::test_resumo_sob_demanda` |

## Cenários esperados

**Exemplo 1 — Compra** ("Estou procurando apartamento na zona sul") — ✅
`test_cenarios_desafio.py::test_exemplo_1_compra` percorre a conversa inteira:
entende a intenção (pergunta compra/aluguel), pergunta quartos, faixa de preço,
usa a região informada, identifica a urgência ("preciso me mudar logo" →
imediata), sugere imóveis e encaminha para visita confirmada com o corretor da zona.

**Exemplo 2 — Investimento** ("Quero investir em imóveis para renda") — ✅
`test_exemplo_2_investimento_vai_para_especialista`: identifica o perfil
investidor, o ticket (R$ 400 mil), a expectativa de retorno (0,6% a.m.),
mostra análise com dados de mercado e direciona para o especialista em
investimentos (Ricardo Moura).

**Exemplo 3 — Follow-up** (cliente parou de responder) — ✅
`test_followup.py`: após o tempo de inatividade o agente retoma o contato pelo
nome e no ponto em que a conversa parou; quando o lead responde, a conversa
continua de onde estava (memória); no Telegram a mensagem chega no chat do cliente.

## Ajustes feitos nesta validação

| Problema encontrado | Correção |
|---|---|
| Exemplo 1: "procurando apartamento" deixava a intenção **indefinida** | O Esclarecedor pergunta "comprar ou alugar?" antes de seguir |
| Exemplo 1: a **urgência** nunca era perguntada e "preciso me mudar logo" causava repetição da pergunta | Urgência passou a ser coletada e classificada em código (imediata / 1-3 meses / 3-6 meses / sem pressa) |
| Exemplo 2: investidor ia para o corretor da região, não para um **especialista** | Novo corretor especialista em investimentos (`COR009`) e regra no Agendador |
| Follow-up: rodava só por script manual, repetia indefinidamente e **não chegava ao chat certo do Telegram** | Follow-up automático no bot, limite de 2 tentativas, mapeamento chat ↔ lead |
| Conversa real no Telegram "se perdeu": "quero mais fotos" virava agendamento, o corretor mudava ao trocar o horário, "quero alugar também" reaproveitava dados da compra, "Ok" voltava a intenção para compra, busca vazia sem saída | Pedido de fotos = detalhes do imóvel mostrado; mesmo corretor na troca de horário; nova busca começa do zero (a anterior fica registrada para o corretor e a visita pendente é lembrada); intenção só muda se o lead disser; valores ("2500", "3 mil") e busca relaxada com sugestão de ajustes (`tests/test_conversa_telegram_0310.py`) |
| Aluguel na Mooca sugeria Santana (Zona Norte) | Busca começa no bairro pedido (inclusive com valores próximos, sinalizados); bairros vizinhos só se o lead quiser ("sim" à pergunta ou "outros bairros"); outra região só em último caso. Investimento compara a rentabilidade estimada (dados de mercado por bairro) e indica onde o retorno é maior (`tests/test_bairro_primeiro_e_retorno.py`) |
| Fotos enviadas sem pedir e texto confuso ("não há na Zona Leste… tenho na Mooca, bairro da Zona Leste") | Lista numerada montada em código com resumo por tipo de busca (investimento: tipo, quartos/suítes, vagas, bairro, valor, retorno mensal e anual; aluguel: aluguel + condomínio; compra: valor de venda + condomínio); fotos só quando o lead pede ("fotos do 2"); zona comparada sem diferenciar maiúsculas |
| Resumo: só era gerado para lead "quente" e o **painel mostrava as últimas mensagens, não o resumo** | Resumo ao confirmar o agendamento, salvo no lead, exibido no Painel e na Área do Corretor, com geração sob demanda |

## Requisitos funcionais e diferenciais (resumo)

- Atendimento conversacional, conversa natural, fluxo humanizado, continuidade, qualificação, agendamento, resumo inteligente e **dashboard** (Painel da Imobiliária: métricas, temperatura, leads, resumos, eventos) — ✅
- Diferenciais: RAG (embeddings locais + TF-IDF), Telegram, memória conversacional, multiagentes (LangGraph), Voice AI (Azure Speech: áudio no Telegram e microfone no Streamlit, com resposta falada), CRM simulado, observabilidade (eventos), segurança (CPF validado e mascarado nos prompts, só segredos no `.env`, demais configurações em `config/settings.toml`), deploy em cloud (Dockerfile) — ✅

## Critérios de avaliação do Tech Challenge (05/10)

Legenda: ✅ forte · 🟡 bom, com ressalva conhecida · ❌ ausente

### Arquitetura

| Critério | Situação | Evidências no projeto | Ressalvas / próximos passos |
|---|---|---|---|
| Organização da solução | ✅ | Clean Architecture em camadas: `src/domain` (entidades, regras puras: CPF, investimento, avaliação de imóvel, localização), `src/agents` (24 módulos, um papel por agente), `src/services` (casos de uso: agenda, carteira, pós-visita, captação, painel), `src/infrastructure` (LLM, RAG, SQLite, Telegram, voz, configuração), `src/interface` (Streamlit) e `src/container.py` (composition root). Configuração separada em `config/settings.toml` (Git) × `.env` (segredos). Interface com **uma tela por módulo** (`src/interface/paginas/`: `chat.py`, `painel.py`, `corretor.py`, `comum.py`; `streamlit_app.py` só faz a navegação). Docs: `README.md`, `docs/ARQUITETURA.md`, este relatório. | — |
| Escalabilidade | 🟡 | Tudo depende de **interfaces** (`ILLMProvider`, `ILeadRepository`, `IAgendaRepository`, `IPropertyRepository`, `IVectorSearch`, `INotifier`, `IVoiceService`, `FonteConfiguracao`): trocar SQLite por PostgreSQL/Cosmos, o CRM simulado por um real ou o .env por Azure Key Vault é criar uma classe nova, sem mexer nos agentes. Decisões em Python (baratas) e LLM só para redigir (menos tokens/latência). Docker + `docker-compose`, configuração por variável de ambiente (12-factor). Bot assíncrono (o LLM roda fora do loop). | Para muitos usuários simultâneos: SQLite e `data/telegram_sessoes.json` são de uma máquina só; o follow-up roda dentro do processo do bot (em produção: fila/agendador — Azure Functions, Celery); leads salvos como JSON e buscados por varredura (`buscar_por_cpf`) — com volume real, CPF em coluna indexada. |
| Componentização | ✅ | Grafo LangGraph com 11 nós especializados (identificação, encerramento, agenda do cliente, captação, qualificador, esclarecedor, consultor, detalhe, agendador, resumidor, recapitulador) + serviços independentes reaproveitados por **duas interfaces** (Streamlit e Telegram usam o mesmo `ConversationService`). Injeção de dependências manual no container; cada componente testável isoladamente (240 testes, LLM simulado, rede simulada com `httpx.MockTransport`). | — |

### Inteligência Artificial

| Critério | Situação | Evidências | Ressalvas |
|---|---|---|---|
| Qualidade das respostas | ✅ | Princípio "**decisões, cálculos e dados em Python; LLM só interpreta e redige**": listas de imóveis, valores, rentabilidade, estimativa de valor, agenda e follow-up com fatos calculados — o LLM não inventa preço, horário nem imóvel. RAG com embeddings locais (sentence-transformers) + proximidade de bairros; busca "bairro primeiro"; dados de mercado com fonte. | Os testes automáticos usam o LLM simulado: a redação final com o Azure (`gpt-4.1-mini`) precisa ser conferida na demonstração. |
| Humanização | ✅ | Persona "Sr. Agim" com nome configurável; trata o cliente pelo nome; reconhece quem volta ("Que bom te ver de novo"); mensagens curtas e calorosas; voz neural em português (Azure Speech) no Telegram e no navegador; pós-visita ("o que achou?"); despedida com resumo e nota; respeita "pare de me mandar mensagens". | — |
| Contexto conversacional | ✅ | Memória persistente por cliente (SQLite) entre sessões **e canais** (identificação por CPF une web e Telegram); histórico recente no prompt; perfil acumulado (intenção, região, quartos, faixa, urgência); referências como "o segundo", "fotos do 2", "esse imóvel"; troca de intenção e recomeço de busca; estados pendentes (proposta de horário, cadastro, cancelamento) guardados em marcadores invisíveis; feedback de visitas muda as próximas sugestões. | — |

### Experiência do Usuário

| Critério | Situação | Evidências | Ressalvas |
|---|---|---|---|
| Interface | ✅ | 3 telas para 3 públicos: **chat do cliente** (fotos em galeria, microfone, barra lateral com o perfil), **Área do Corretor** (7 abas + assistente com menu), **Painel da Imobiliária** (KPIs, funil, gráficos, filtros). Telegram como canal móvel (texto, fotos, áudio, comandos `/start`, `/novo`, `/encerrar`, `/followup`, `/posvisita`). | Visual padrão do Streamlit (sem identidade visual própria da imobiliária). |
| Clareza | ✅ | Listas numeradas com o essencial por tipo de negócio; confirmação antes de qualquer cancelamento; mensagens de erro que dizem o motivo (rede, chave, região); indicador 🟢/🟡 de LLM real ou simulado e 🎤/🔇 de voz. | — |
| Usabilidade | ✅ | Linguagem natural em tudo (cliente marca, remarca e cancela pelo chat; corretor registra resultado e capta imóveis pelo chat) **e** atalhos (menu numerado do corretor, botões, formulários); o cliente encerra quando quiser; funciona sem nenhuma chave (modo simulado) para quem clonar o projeto. | Não há login/senha (POC): o corretor escolhe o nome numa lista. |

### Inovação

| Critério | Situação | Evidências |
|---|---|---|
| Criatividade | ✅ | Vai além do SDR pedido: **captação** (cliente que quer vender/alugar o próprio imóvel, com estimativa de valor e avaliação agendada), **match** de imóvel novo × clientes, **demanda × oferta** para orientar a captação, **pós-visita** que realimenta as sugestões, follow-up de retomada com argumentos reais, investimento comparado à poupança com dados de mercado. |
| Diferenciais técnicos | ✅ | Multiagentes com LangGraph; RAG com embeddings locais e gratuitos; Azure OpenAI; Voice AI (Azure Speech via REST, OGG/Opus do Telegram sem ffmpeg); Telegram; CRM simulado; observabilidade (eventos, painel do agente de IA, satisfação); configuração segura (segredo em arquivo versionado é recusado na inicialização); Docker; 240 testes automatizados. |

### Resumo

Todos os critérios estão atendidos. Ressalvas a declarar na apresentação (são escolhas de POC, não falhas): SQLite/arquivo local e follow-up dentro do processo do bot (escala de uma instância), interface no visual padrão do Streamlit e login simplificado do corretor.

## Limitações conhecidas

- Os testes automáticos usam o LLM simulado; a qualidade do texto com o Azure OpenAI deve ser conferida na demonstração.
- Leads só da web recebem o follow-up no histórico (veem ao voltar à página); o envio ativo acontece pelo Telegram.
- Fotos dos imóveis são ilustrativas (geradas ou de banco de imagens livre).
