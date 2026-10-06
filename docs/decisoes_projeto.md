# Decisões do Projeto — Sr. Agim

Este é o registro das **decisões que deram forma ao projeto**. Cada uma segue o formato de um ADR (*Architecture Decision Record*): o contexto, o que foi decidido, as alternativas que consideramos e o que ganhamos ou pagamos com a escolha.

O contexto do negócio está em [`contexto_projeto.md`](contexto_projeto.md) e os diagramas em [`ARQUITETURA.md`](ARQUITETURA.md).

## Índice

| # | Decisão | Tema |
|---|---|---|
| [D01](#d01--clean-architecture-com-solid) | Clean Architecture com SOLID | Arquitetura |
| [D02](#d02--multiagentes-com-langgraph-em-vez-de-um-prompt-único) | Multiagentes com LangGraph | IA |
| [D03](#d03--o-python-decide-o-llm-conversa) | O Python decide, o LLM conversa | IA |
| [D04](#d04--identificação-por-cpf-sem-llm) | Identificação por CPF sem LLM | IA / segurança |
| [D05](#d05--azure-openai-gpt-41-mini-com-provedor-simulado) | Azure OpenAI com provedor simulado | IA / custo |
| [D06](#d06--busca-sql-primeiro-rag-como-rede-de-segurança) | SQL primeiro, RAG como rede de segurança | IA / dados |
| [D07](#d07--embeddings-locais-e-gratuitos) | Embeddings locais e gratuitos | IA / custo |
| [D08](#d08--sqlite-para-a-poc-atrás-de-interfaces) | SQLite atrás de interfaces | Dados |
| [D09](#d09--memória-no-lead-estado-do-grafo-descartável) | Memória no Lead, estado descartável | IA / dados |
| [D10](#d10--streamlit--telegram-sobre-o-mesmo-núcleo) | Streamlit + Telegram no mesmo núcleo | UX |
| [D11](#d11--corretor-escolhido-por-zona-e-carga) | Corretor por zona e carga | Negócio |
| [D12](#d12--follow-up-com-limite-argumentos-reais-e-opt-out) | Follow-up com limite, argumentos e opt-out | Negócio / ética |
| [D13](#d13--agentes-de-entrada-determinísticos-antes-da-qualificação) | Agentes de entrada antes da qualificação | IA |
| [D14](#d14--marcadores-invisíveis-nas-mensagens) | Marcadores invisíveis nas mensagens | UX |
| [D15](#d15--configuração-em-três-camadas-segredos-só-no-env) | Configuração em três camadas | Segurança |
| [D16](#d16--voz-pela-api-rest-do-azure-speech) | Voz pela API REST | IA / infraestrutura |
| [D17](#d17--captação-de-imóveis-como-fluxo-próprio) | Captação como fluxo próprio | Negócio |
| [D18](#d18--resultado-da-visita-realimenta-a-busca) | Resultado da visita realimenta a busca | Negócio / IA |
| [D19](#d19--rotinas-automáticas-dentro-do-bot) | Rotinas dentro do bot | Infraestrutura |
| [D20](#d20--testes-sem-rede-e-sem-o-banco-real) | Testes sem rede e sem o banco real | Qualidade |
| [D21](#d21--interface-separada-por-tela) | Interface separada por tela | Organização |

---

## D01 — Clean Architecture com SOLID

**Contexto.** Um agente de IA tende a virar um arquivo gigante que mistura prompt, banco, regra de negócio e tela. Isso dificulta testar e trocar peças, por exemplo trocar o provedor de LLM.

**Decisão.** Cinco camadas: interface → serviços → agentes → domínio, e infraestrutura implementando as interfaces do domínio. Todas as dependências são montadas em um só lugar, o `src/container.py` (composition root).

Como cada princípio aparece no código está detalhado em [SOLID.md](SOLID.md).

**Alternativas.** Um script único com LangChain (mais rápido no início, difícil de manter); um framework com injeção automática (complexidade desnecessária para uma POC).

**Consequências.**
- ✅ Agentes testados com LLM simulado e bancos temporários, sem mudar código.
- ✅ Trocar SQLite, o CRM ou o LLM é escrever uma classe nova.
- ➖ Mais arquivos e alguma cerimônia (interfaces, container) para quem chega ao projeto.

---

## D02 — Multiagentes com LangGraph em vez de um prompt único

**Contexto.** O atendimento tem etapas bem diferentes: identificar, qualificar, buscar imóveis, agendar e resumir. Um prompt único que "faz tudo" fica longo, imprevisível e difícil de depurar.

**Decisão.** Um grafo LangGraph com 11 nós especializados e funções de roteamento em Python (`_rotear_entrada`, `_rotear_apos_qualificacao`, ...) atuando como supervisor.

**Alternativas.** Um prompt único com *function calling*; um agente ReAct que escolhe sozinho as ferramentas. As duas deixam o controle do fluxo com o modelo.

**Consequências.**
- ✅ Prompts curtos e focados; o caminho de cada mensagem é previsível e testável.
- ✅ Um agente novo é um nó e uma aresta.
- ➖ O roteamento precisa ser mantido à mão quando surgem novos casos.
- ⚠️ Versões novas do LangGraph proíbem um nó com o mesmo nome de uma chave do estado. Por isso existe o `tests/test_grafo_nomes.py`.

---

## D03 — O Python decide, o LLM conversa

**Contexto.** LLMs escrevem bem, mas inventam: um preço, um horário livre, um imóvel que não existe. No mercado imobiliário, isso destrói a confiança na hora.

**Decisão.** Tudo o que é **fato ou decisão** fica em Python: filtrar imóveis, calcular rentabilidade, estimar valor, escolher corretor, checar conflito de agenda, decidir quem recebe follow-up. O LLM recebe esses fatos prontos e só **interpreta** a mensagem do cliente e **redige** a resposta.

**Alternativas.** Deixar o LLM consultar a base por ferramentas e decidir sozinho.

**Consequências.**
- ✅ Nada de imóvel, preço ou horário inventado.
- ✅ Menos tokens e menos latência: o prompt carrega só o essencial.
- ✅ As regras podem ser testadas com casos exatos.
- ➖ Mais código Python para manter.

---

## D04 — Identificação por CPF sem LLM

**Contexto.** O cliente precisa ser reconhecido quando volta, inclusive por outro canal. Um LLM pode confundir um telefone com CPF ou "corrigir" um dígito.

**Decisão.** O `IdentificacaoAgent` é uma máquina de estados determinística. O CPF é validado com o algoritmo oficial dos dígitos verificadores (`src/domain/cpf.py`). No LLM, o CPF vai mascarado.

**Consequências.**
- ✅ Um cliente, uma ficha, em qualquer canal.
- ✅ Nenhum dado de identidade passa "em claro" pelo modelo.
- ➖ O cliente precisa informar o CPF logo no começo.

---

## D05 — Azure OpenAI (`gpt-4.1-mini`) com provedor simulado

**Contexto.** O desafio sugere o ecossistema Azure. Ao mesmo tempo, o avaliador precisa conseguir rodar o projeto sem chave e sem custo.

**Decisão.** `ILLMProvider` com duas implementações: `AzureOpenAIProvider` e `MockLLMProvider`. Com `llm.provider = "auto"`, o projeto usa o Azure se encontrar a chave e o mock caso contrário. A tela e o terminal mostram qual está ativo. O modelo escolhido foi o `gpt-4.1-mini`: bom em português, rápido e barato para conversa.

**Consequências.**
- ✅ Qualquer pessoa roda o projeto inteiro; com a chave, a IA real entra sem mudar código.
- ➖ Os testes automáticos não avaliam a redação real do Azure, que precisa ser conferida na demonstração.

---

## D06 — Busca SQL primeiro, RAG como rede de segurança

**Contexto.** A maioria dos pedidos é objetiva ("2 quartos na Mooca até 500 mil"); alguns são vagos ("algo aconchegante perto de um parque").

**Decisão.** Primeiro um `SELECT` parametrizado por negócio, região, quartos e preço, começando pelo bairro pedido, depois os vizinhos e por último a zona. Quando não acha nada ou o pedido é livre, entra a busca semântica (RAG).

**Consequências.**
- ✅ Respostas rápidas e exatas no caso comum; flexibilidade no caso vago.
- ✅ O cliente não recebe Santana quando pediu Mooca.

---

## D07 — Embeddings locais e gratuitos

**Contexto.** Embeddings na nuvem custam por chamada e exigem mais uma chave.

**Decisão.** `sentence-transformers` com o modelo multilíngue `paraphrase-multilingual-MiniLM-L12-v2`, rodando na própria máquina e com cache em disco. Azure fica como opção e TF-IDF como reserva automática se o modelo falhar.

**Consequências.**
- ✅ RAG funcionando sem custo e sem rede.
- ➖ A instalação baixa o PyTorch (CPU), o que deixa o primeiro `pip install` mais pesado.

---

## D08 — SQLite para a POC, atrás de interfaces

**Contexto.** A POC precisa de persistência real (memória, agenda, eventos) sem exigir que o avaliador suba um banco.

**Decisão.** Dois arquivos SQLite (`agente_sdr.db` e `imoveis.db`), criados e populados sozinhos na primeira execução, sempre acessados por repositórios (`ILeadRepository`, `IAgendaRepository`, `IPropertyRepository`...). Colunas novas entram por migração automática.

**Consequências.**
- ✅ Zero configuração.
- ➖ Serve a uma instância só. Para escalar: Azure SQL ou Cosmos DB, com o CPF indexado.

---

## D09 — Memória no Lead, estado do grafo descartável

**Contexto.** A conversa precisa continuar de onde parou, mesmo depois de reiniciar o programa ou de trocar de canal.

**Decisão.** A memória de longo prazo é o `Lead` salvo no SQLite: histórico, perfil, feedbacks e captação. O `EstadoConversa` do LangGraph é reconstruído a cada mensagem e descartado no fim.

**Consequências.**
- ✅ Nenhum contexto se perde.
- ✅ O grafo não precisa de *checkpointer*.
- ➖ Cada rodada relê o lead (custo desprezível na escala da POC).

---

## D10 — Streamlit + Telegram sobre o mesmo núcleo

**Contexto.** O desafio pede interface e sugere Telegram. O corretor e o gestor precisam de telas mais ricas que um chat.

**Decisão.** Streamlit para o chat web, a Área do Corretor e o Painel; Telegram como canal móvel do cliente. Os dois usam o mesmo `ConversationService`.

**Consequências.**
- ✅ Uma regra implementada uma vez vale nos dois canais.
- ✅ O cliente começa na web e continua no celular.
- ➖ O visual é o padrão do Streamlit.

---

## D11 — Corretor escolhido por zona e carga

**Contexto.** Mandar todo mundo para o mesmo corretor sobrecarrega uma pessoa; mandar ao acaso faz alguém atravessar a cidade.

**Decisão.** O corretor da zona do imóvel; havendo mais de um, o com menos agendamentos ativos; não havendo nenhum, o de menor carga geral. Investidores vão para o especialista em investimentos. Ao remarcar, o corretor é mantido.

**Consequências.**
- ✅ Distribuição justa e coerente com o território de cada corretor.

---

## D12 — Follow-up com limite, argumentos reais e opt-out

**Contexto.** Follow-up de menos perde o cliente; follow-up demais vira spam.

**Decisão.**
- Recebe follow-up quem parou de responder e **não tem visita marcada**.
- No máximo **2** mensagens, ou **3** quando ficou um negócio pela metade (compra, aluguel, investimento ou captação), mesmo que o cliente tenha encerrado a conversa.
- A mensagem de retomada usa **fatos reais da base** (`ArgumentosFollowUp`), nunca promessas inventadas.
- Quem pede para não ser contatado nunca mais recebe mensagem (`nao_contatar`).

**Consequências.**
- ✅ Insistência na medida, com respeito ao cliente.

---

## D13 — Agentes de entrada determinísticos antes da qualificação

**Contexto.** Antes de qualificar, a mensagem pode ser "quero encerrar", "cancela minha visita de sexta" ou "quero vender meu apartamento". Mandar isso ao qualificador geraria respostas fora de contexto.

**Decisão.** Uma cadeia de entrada: encerramento → identificação → agenda do cliente → captação → qualificador. Cada nó só responde se a mensagem for dele. Ações sensíveis, como cancelar, pedem confirmação.

**Consequências.**
- ✅ O cliente resolve a agenda dele pelo chat, sem cair num fluxo de busca.
- ➖ A detecção por padrões precisou de ajustes. Exemplo: "perto do meu trabalho" não pode virar captação.

---

## D14 — Marcadores invisíveis nas mensagens

**Contexto.** Algumas respostas precisam virar fotos, cartões ou listas, e alguns estados precisam sobreviver entre mensagens (um cancelamento aguardando confirmação, um pós-visita aguardando resposta).

**Decisão.** Marcadores como `[[FOTOS:...]]`, `[[LISTA:...]]`, `[[AGENDA:...]]`, `[[POSVISITA:...]]` e `[[ENCERRADO:...]]`. A interface os transforma em elementos visuais, e o `src/domain/midia.py` os remove antes de mostrar o texto e antes de mandar o histórico ao LLM.

**Consequências.**
- ✅ Estado conversacional sem tabelas extras; o mesmo marcador funciona no Streamlit e no Telegram.

---

## D15 — Configuração em três camadas, segredos só no `.env`

**Contexto.** O `.env` original misturava chaves com configurações comuns, e nada impedia uma chave de ir para o Git.

**Decisão.** `config/settings.toml` (versionado) → `config/settings.local.toml` (só da máquina) → `.env` e variáveis de ambiente (só segredos). Cada campo de `Settings` declara se é segredo. Segredo achado num TOML faz o app **recusar a inicialização**. As fontes são adaptadores atrás do protocolo `FonteConfiguracao`.

**Consequências.**
- ✅ Configuração revisável no Git, segredos protegidos e tudo sobrescrevível por variável de ambiente (Docker, Azure).

---

## D16 — Voz pela API REST do Azure Speech

**Contexto.** O SDK nativo do Azure Speech dava problema de instalação, e o Telegram manda áudio em OGG/Opus.

**Decisão.** Chamadas REST diretas com `httpx` (fala→texto e texto→fala, voz `pt-BR-AntonioNeural`). O WAV do navegador é convertido para 16 kHz mono no próprio Python. Os erros são traduzidos para o português.

**Consequências.**
- ✅ Sem dependência nativa nem ffmpeg; testável com `httpx.MockTransport`.

---

## D17 — Captação de imóveis como fluxo próprio

**Contexto.** Nem todo mundo que chega quer comprar ou alugar. Proprietários também procuram a imobiliária, e são eles que trazem imóvel novo para a carteira.

**Decisão.** O `CaptacaoImovelAgent` coleta tipo, quartos, banheiros, vagas, metragem e endereço. A função de domínio `estimar_valor` calcula uma faixa com imóveis comparáveis (bairro → vizinhos → zona) e o R$/m² do bairro. Em seguida, agenda uma **avaliação** com o corretor da zona. Quando o corretor marca como "captado", o imóvel é publicado na base e cruzado com os clientes interessados.

**Consequências.**
- ✅ Fecha o ciclo demanda ↔ oferta.
- ➖ A estimativa é indicativa; o valor final vem da avaliação presencial, e o agente deixa isso claro.

---

## D18 — Resultado da visita realimenta a busca

**Contexto.** Se o cliente achou caro, mostrar imóveis do mesmo preço é desperdício.

**Decisão.** O resultado da visita (gostou, proposta, fechado, não gostou + motivo, não compareceu) é registrado pelo corretor ou capturado no pós-visita. A função `aplicar_feedback` usa o motivo nas buscas seguintes: preço menor, mais metragem, outro bairro.

**Consequências.**
- ✅ A conversa aprende com a visita.
- ✅ O mesmo registro alimenta o funil do corretor e o Painel.

---

## D19 — Rotinas automáticas dentro do bot

**Contexto.** Follow-up e pós-visita precisam rodar sozinhos, mas a POC não deveria exigir um agendador externo.

**Decisão.** Uma tarefa assíncrona no `telegram_bot.py` (primeira verificação ~20 s após subir, depois no intervalo configurado), além dos comandos `/followup` e `/posvisita` e do script `scripts/executar_followup.py`.

**Consequências.**
- ✅ Funciona ao rodar o bot, sem infraestrutura extra.
- ➖ Sem o bot no ar, não há rotina automática. Em produção: Azure Functions ou Container Apps Jobs.

---

## D20 — Testes sem rede e sem o banco real

**Contexto.** Os testes precisam rodar em qualquer lugar, de graça, e não podem atrapalhar o app aberto.

**Decisão.** LLM simulado, `httpx.MockTransport` para Speech e Telegram, e bancos temporários criados por teste (`dataclasses.replace` nos settings). As chamadas reais ficam em scripts de diagnóstico (`scripts/diagnostico_conexao.py`, `scripts/testar_voz.py`).

**Consequências.**
- ✅ 240 testes rápidos e reprodutíveis, incluindo a interface (AppTest do Streamlit).
- ➖ A qualidade do texto real do Azure é verificada manualmente.

---

## D21 — Interface separada por tela

**Contexto.** O `streamlit_app.py` tinha crescido demais, com as três telas no mesmo arquivo.

**Decisão.** O `streamlit_app.py` (~80 linhas) só faz a navegação; cada tela fica em `src/interface/paginas/` (`chat.py`, `corretor.py`, `painel.py`, `comum.py`).

**Consequências.**
- ✅ Cada tela pode evoluir sem conflito com as outras.
