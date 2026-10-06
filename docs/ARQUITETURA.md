# Arquitetura do Sr. Agim

Este documento conta **como o Sr. Agim foi pensado e por quê**. O `README.md` explica como instalar e rodar; aqui a conversa é sobre desenho: camadas, agentes, fluxos e as decisões por trás de cada um.

Os fluxos estão em diagramas (Mermaid), que o GitHub e o VS Code desenham sozinhos. Cada diagrama vem com um parágrafo curto explicando o que ele mostra.

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Camadas (Clean Architecture)](#2-camadas-clean-architecture)
3. [Como as peças são montadas](#3-como-as-peças-são-montadas-composition-root)
4. [O caminho de uma mensagem](#4-o-caminho-de-uma-mensagem)
5. [O grafo multiagente (LangGraph)](#5-o-grafo-multiagente-langgraph)
6. [Fluxos do cliente](#6-fluxos-do-cliente)
7. [Fluxos do corretor](#7-fluxos-do-corretor)
8. [Rotinas automáticas](#8-rotinas-automáticas)
9. [Voz](#9-voz-azure-speech)
10. [Dados e memória](#10-dados-e-memória)
11. [Configuração e segurança](#11-configuração-e-segurança)
12. [Observabilidade](#12-observabilidade)
13. [Escalabilidade e caminho para produção](#13-escalabilidade-e-caminho-para-produção)
14. [Mapa de componentes](#14-mapa-de-componentes)

---

## 1. Visão geral

Três tipos de gente usam o sistema: o **cliente**, que conversa pelo navegador ou pelo Telegram; o **corretor**, que trabalha na Área do Corretor; e o **gestor**, que acompanha tudo pelo Painel da Imobiliária. Por trás, um único núcleo atende todos eles.

```mermaid
flowchart LR
    cliente(["👤 Cliente"])
    corretor(["🧑‍💼 Corretor"])
    gestor(["📊 Gestor"])

    subgraph canais["Canais"]
        web["Streamlit<br/>chat · Área do Corretor · Painel"]
        tg["Bot do Telegram<br/>texto e áudio"]
    end

    subgraph nucleo["Núcleo do Sr. Agim"]
        servicos["Serviços de aplicação"]
        grafo["Grafo multiagente<br/>(LangGraph)"]
        rotinas["Rotinas automáticas<br/>follow-up · pós-visita"]
    end

    subgraph dados["Dados locais"]
        db[("SQLite<br/>leads · agenda · eventos")]
        imoveis[("SQLite<br/>imóveis")]
        json[("JSON<br/>bairros · mercado · corretores")]
    end

    subgraph azure["Azure"]
        openai["Azure OpenAI<br/>gpt-4.1-mini"]
        speech["Azure Speech<br/>voz ↔ texto"]
    end

    cliente --> web & tg
    corretor --> web
    gestor --> web
    web & tg --> servicos
    servicos --> grafo
    rotinas --> servicos
    grafo & servicos --> db & imoveis & json
    grafo --> openai
    tg & web --> speech
```

Sem chave do Azure, o projeto troca o OpenAI por um **provedor simulado (mock)** e continua funcionando. É assim que os testes rodam sem internet.

---

## 2. Camadas (Clean Architecture)

O código está dividido em cinco camadas. A regra de ouro: **as setas de dependência apontam para dentro**. O domínio não conhece banco, Azure, Telegram nem Streamlit; quem conhece esses detalhes é a infraestrutura, que *implementa* as interfaces do domínio.

```mermaid
flowchart TB
    subgraph L1["Interface — src/interface/ · telegram_bot.py"]
        I1["streamlit_app.py (navegação)"]
        I2["paginas/ chat · corretor · painel"]
        I3["telegram_bot.py"]
    end

    subgraph L2["Serviços de aplicação — src/services/"]
        S1["ConversationService"]
        S2["AgendaService · PosVisitaService · CaptacaoService"]
        S3["CarteiraCorretor · MatchImoveis · Desempenho · PainelGestor"]
    end

    subgraph L3["Agentes — src/agents/"]
        A1["graph.py (LangGraph)"]
        A2["agentes do cliente"]
        A3["agentes do corretor e follow-up"]
    end

    subgraph L4["Domínio — src/domain/"]
        D1["entities.py (Lead, Imovel, Agendamento...)"]
        D2["interfaces.py (ILLMProvider, IAgendaRepository...)"]
        D3["regras puras: cpf · avaliacao_imovel · feedback_visita · investimento"]
    end

    subgraph L5["Infraestrutura — src/infrastructure/"]
        F1["llm/ Azure OpenAI · Mock"]
        F2["rag/ embeddings · TF-IDF"]
        F3["memory/ · repositories/ SQLite e JSON"]
        F4["notifications/ Telegram · voice/ Speech"]
        F5["crm/ · observability/ · configuracao.py"]
    end

    L1 --> L2 --> L3 --> L4
    L5 -. "implementa as interfaces" .-> L4
```

**Por que isso importa na prática?**

- **Testável:** os 240 testes trocam o Azure pelo `MockLLMProvider` e o banco real por bancos temporários, sem mudar uma linha dos agentes.
- **Trocável:** o `IPropertyRepository` tem duas implementações (`SqlitePropertyRepository` e `JsonPropertyRepository`). Trocar uma pela outra não mexe em nada acima. Esse é o Open/Closed e o Liskov aplicados de verdade.
- **Regra de negócio fora do LLM:** validar CPF, estimar o valor de um imóvel e classificar o feedback de uma visita são funções puras no domínio, testadas com casos exatos.

### Os princípios SOLID no projeto

O detalhamento de cada princípio, com arquivos, classes e trechos reais do código, está em [SOLID.md](SOLID.md).

| Princípio | Onde aparece |
|---|---|
| **S** — responsabilidade única | Um agente por tarefa (identificar, qualificar, agendar...); um serviço por caso de uso |
| **O** — aberto/fechado | Novo agente = novo nó no grafo; nova fonte de configuração = nova classe `FonteConfiguracao` |
| **L** — substituição de Liskov | `MockLLMProvider` ↔ `AzureOpenAIProvider`; `JsonPropertyRepository` ↔ `SqlitePropertyRepository` |
| **I** — segregação de interfaces | Interfaces pequenas: `IVectorSearch` só busca; `INotifier` só envia; `IVoiceService` só fala e ouve |
| **D** — inversão de dependência | Agentes recebem interfaces no construtor; quem decide a implementação é o `container.py` |

---

## 3. Como as peças são montadas (composition root)

Ninguém dá `new` em dependência dentro de agente. Tudo é montado **num único lugar**, o `src/container.py`, que lê a configuração, escolhe as implementações e entrega cada objeto pronto para quem precisa.

```mermaid
flowchart LR
    cfg["src/config.py<br/>Settings"] --> c["AppContainer<br/>(container.py)"]

    c --> llm{"usando_llm_real?"}
    llm -- "sim" --> az["AzureOpenAIProvider"]
    llm -- "não" --> mk["MockLLMProvider"]

    c --> rag{"embeddings.provider"}
    rag -- "local / azure" --> emb["EmbeddingVectorSearch"]
    rag -- "falhou ou tfidf" --> tf["TfidfVectorSearch"]

    c --> voz{"tem chave do Speech?"}
    voz -- "sim" --> sp["AzureSpeechService"]
    voz -- "não" --> sv["sem voz"]

    c --> repos["Repositórios SQLite<br/>leads · agenda · imóveis · corretores"]
    c --> grafo["construir_grafo_sdr(...)"]
    c --> svc["ConversationService · AgendaService<br/>PosVisita · Captacao · Match · Desempenho<br/>PainelGestor · MenuCorretorAgent · FollowUpAgent"]

    az & mk & emb & tf & repos --> grafo
    grafo --> svc
    sp --> svc
```

O Streamlit e o bot do Telegram só fazem `obter_container()` e usam os serviços. Ao subir, os dois mostram qual LLM está ativo (Azure ou Mock) e se a voz está ligada.

---

## 4. O caminho de uma mensagem

Este é o fluxo de uma rodada de conversa, do clique em "enviar" até a resposta na tela.

```mermaid
sequenceDiagram
    autonumber
    actor C as Cliente
    participant UI as Streamlit / Telegram
    participant CS as ConversationService
    participant G as Grafo LangGraph
    participant AG as Agentes
    participant LLM as Azure OpenAI / Mock
    participant R as Repositórios (SQLite)
    participant CRM as CRM simulado
    participant OBS as Eventos

    C->>UI: mensagem (texto ou áudio já transcrito)
    UI->>CS: processar_mensagem(lead, texto)
    CS->>OBS: evento "mensagem_recebida"
    CS->>CS: monta o EstadoConversa a partir do Lead salvo
    CS->>G: invoke(estado_inicial)
    G->>AG: roteia para o agente certo
    AG->>R: consulta imóveis, agenda, corretores
    AG->>LLM: interpreta / redige (com dados já calculados)
    LLM-->>AG: texto
    AG-->>G: campos novos do estado
    G-->>CS: estado_final
    CS->>CS: troca de lead se o CPF já existia
    CS->>CS: aplica no Lead (perfil, agenda, captação, encerramento...)
    CS->>R: salva o Lead
    CS->>CRM: registrar_lead
    CS->>OBS: evento "turno_processado"
    CS-->>UI: Lead atualizado
    UI-->>C: resposta (fotos e listas a partir dos marcadores)
```

Dois detalhes desse fluxo:

- **O estado do grafo é descartável.** Ele vive só durante uma rodada. A memória de verdade é o `Lead` salvo no SQLite, e o estado é reconstruído a cada mensagem. Por isso nada se perde se o programa reiniciar.
- **Marcadores invisíveis.** O agente pode deixar na resposta marcadores como `[[FOTOS:...]]`, `[[LISTA:...]]` ou `[[AGENDA:...]]`. A interface os troca por fotos, cartões e listas, e o `src/domain/midia.py` os remove antes de mostrar o texto ou mandar para o LLM.

---

## 5. O grafo multiagente (LangGraph)

### Por que vários agentes e não um prompt gigante?

Um SDR de verdade faz várias coisas diferentes: descobre quem é o cliente, entende o que ele quer, mostra imóveis, agenda, passa o bastão para o corretor. Separar cada tarefa num agente dá:

- **prompts curtos e focados**, mais fáceis de testar e corrigir;
- **controle de fluxo explícito**: quem decide o próximo passo são funções Python legíveis (`_rotear_entrada`, `_rotear_apos_qualificacao`...), não o LLM;
- **extensão sem efeito cascata**: um agente novo é um nó e uma aresta.

### O grafo real (11 nós)

```mermaid
flowchart TD
    inicio(["mensagem do cliente"]) --> e{"_rotear_entrada"}

    e -- "quer encerrar<br/>ou mandou a nota 1–5" --> enc["encerramento"]
    e -- "ainda não identificado" --> idf["identificacao"]
    e -- "identificado" --> ag["agenda_cliente"]

    ag -- "era sobre a agenda dele<br/>(ver · cancelar · remarcar · feedback)" --> fim(["FIM"])
    ag -- "não era" --> cap["captacao_imovel"]

    cap -- "quer vender/alugar<br/>o próprio imóvel" --> fim
    cap -- "não era" --> q["qualificador"]

    q --> rq{"_rotear_apos_qualificacao"}
    rq -- "pediu resumo" --> rec["recapitulador"]
    rq -- "quer agendar e lead<br/>morno ou quente" --> agd["agendador"]
    rq -- "pediu detalhes<br/>de um imóvel" --> det["detalhe_imovel"]
    rq -- "faltam dados" --> esc["esclarecedor"]
    rq -- "dados completos" --> cons["consultor_imoveis"]

    det -- "achou o imóvel" --> fim
    det -- "não soube qual" --> cons

    cons -- "quer agendar" --> agd
    cons -- "senão" --> fim

    agd -- "confirmado ou<br/>lead quente" --> res["resumidor"]
    agd -- "senão" --> fim

    enc & idf & esc & rec & res --> fim

    classDef regra fill:#e8f1ff,stroke:#3b6fd8,color:#1b2a4a
    classDef llm fill:#fff4e0,stroke:#d18b00,color:#3d2a00
    class enc,idf,ag,cap regra
    class q,esc,cons,det,agd,res,rec llm
```

<sub>🟦 azul = determinístico (regras em Python, sem LLM decidindo) · 🟧 laranja = usa o LLM para interpretar ou redigir</sub>

Os nós de **entrada** (encerramento, identificação, agenda do cliente e captação) mexem em dados sensíveis: CPF, compromissos marcados, cadastro do imóvel. Por isso são determinísticos e não podem "alucinar". Os nós de **conversa** usam o LLM, mas sempre a partir de dados que o Python já buscou e calculou.

### O estado compartilhado

Todos os agentes trabalham sobre um único `EstadoConversa` (`src/agents/state.py`), uma espécie de prancheta que passa de mão em mão. Cada agente lê o que precisa e devolve só o que descobriu; o LangGraph junta essas partes no estado.

```mermaid
flowchart LR
    subgraph estado["EstadoConversa (prancheta)"]
        direction TB
        s1["mensagem_usuario · historico"]
        s2["cliente_identificado · cliente_cpf · cliente_nome"]
        s3["intencao · regiao · quartos · faixa de preço · temperatura"]
        s4["quer_agendar · pediu_detalhes · pediu_resumo"]
        s5["imoveis_sugeridos · agendamento_status"]
        s6["captacao · atendimento_encerrado · nota · nao_contatar"]
        s7["resposta_agente"]
    end
    ag1["agente A"] -- "lê" --> estado
    estado -- "devolve só<br/>o que mudou" --> ag2["agente B"]
```

> **Cuidado conhecido:** versões novas do LangGraph proíbem um nó com o mesmo nome de uma chave do estado. Por isso o nó se chama `captacao_imovel` (a chave é `captacao`). O teste `tests/test_grafo_nomes.py` impede que isso volte.

### O princípio que guia tudo: o Python decide, o LLM conversa

```mermaid
flowchart LR
    m["mensagem"] --> py["Python<br/>extrai, filtra, calcula, decide"]
    py --> fatos["fatos prontos<br/>imóveis · horários · valores · corretor"]
    fatos --> llm["LLM<br/>redige em linguagem natural"]
    llm --> resp["resposta"]
    py -. "o LLM nunca inventa" .-> x["preço, agenda,<br/>CPF, corretor"]
```

---

## 6. Fluxos do cliente

### 6.1 Identificação (nome + CPF)

O `IdentificacaoAgent` é uma pequena máquina de estados, sem LLM. O CPF passa pela validação oficial dos dígitos (`src/domain/cpf.py`). Se o CPF já existe, o cliente recupera todo o histórico e vê na hora as visitas que já tem marcadas, com o nome de cada corretor.

```mermaid
stateDiagram-v2
    [*] --> PedeCPF: primeira mensagem
    PedeCPF --> PedeCPF: CPF inválido
    PedeCPF --> Recorrente: CPF já cadastrado
    PedeCPF --> PedeNome: CPF novo
    PedeNome --> PedeNome: não entendeu o nome
    PedeNome --> Novo: nome ok
    Recorrente --> Identificado: boas-vindas de volta<br/>+ agendas com corretores<br/>+ avisos pendentes
    Novo --> Identificado: boas-vindas
    Identificado --> [*]
```

Quando o cliente é recorrente, a troca de lead acontece no serviço, não no grafo: `ConversationService._resolver_lead_alvo` passa a usar o lead antigo, com todo o histórico, no lugar do lead temporário daquela sessão.

### 6.2 Busca de imóveis: SQL primeiro, RAG como rede de segurança

```mermaid
flowchart TD
    p["perfil do lead<br/>intenção · região · quartos · preço"] --> sql["SqlitePropertyRepository.buscar<br/>SELECT parametrizado"]
    sql --> ok{"achou?"}
    ok -- "sim" --> fb["aplicar_feedback<br/>tira imóveis já recusados<br/>e ajusta por motivo"]
    ok -- "não" --> rag["RAG: buscar_similares<br/>embeddings locais (padrão)<br/>ou Azure · TF-IDF de reserva"]
    rag --> fb
    fb --> calc["Python calcula<br/>rentabilidade · vizinhança · comparação"]
    calc --> llm["LLM redige a sugestão"]
    llm --> out["resposta + [[CAPAS]] / [[FOTOS]]"]
```

A busca estruturada é rápida e previsível; o RAG entra quando o pedido é mais livre ("algo aconchegante perto de um parque"). Se o cliente já visitou e não gostou porque "era caro", a próxima busca já vem filtrada por preço menor. Isso é o `aplicar_feedback`, do `src/domain/feedback_visita.py`.

### 6.3 Agendamento: qual corretor vai?

```mermaid
flowchart TD
    a["cliente quer agendar"] --> z["descobre a zona<br/>do imóvel sugerido<br/>(ou da região de interesse)"]
    z --> bz["corretores dessa zona"]
    bz --> n{"quantos?"}
    n -- "1" --> c1["esse corretor"]
    n -- "mais de 1" --> carga["o de menor carga<br/>contar_agendamentos_ativos"]
    n -- "nenhum" --> coringa["coringa:<br/>menor carga entre todos"]
    c1 & carga & coringa --> conf{"conflito de horário?"}
    conf -- "sim" --> outro["sugere outro horário"]
    conf -- "não" --> reg["registra o Agendamento<br/>+ CRM + resumo para o corretor"]
```

### 6.4 Ciclo de vida de um agendamento

Um agendamento pode ser **reunião**, **visita** ou **avaliação** (captação). O cliente pode ver, cancelar (com confirmação) e remarcar; o corretor também. Quem não fez a alteração é avisado.

```mermaid
stateDiagram-v2
    [*] --> Sugerido
    Sugerido --> Confirmado: cliente aceita o horário
    note right of Sugerido: avaliação (captação) já nasce confirmada
    Confirmado --> Confirmado: remarcado<br/>(checa conflito ±1 h)
    Sugerido --> Cancelado
    Confirmado --> Cancelado: cliente ou corretor cancela
    Confirmado --> Realizado: passou a data
    Realizado --> ResultadoRegistrado: corretor registra<br/>ou cliente responde o pós-visita
    Cancelado --> [*]
    ResultadoRegistrado --> [*]
```

### 6.5 Avisos entre cliente e corretor

```mermaid
sequenceDiagram
    actor Cl as Cliente
    participant AC as AgendaClienteAgent
    participant AS as AgendaService
    participant BD as Agenda (SQLite)
    actor Co as Corretor
    participant TG as Telegram

    Note over Cl,Co: Cliente cancela ou remarca
    Cl->>AC: "quero cancelar a visita de sexta"
    AC->>Cl: confirma?
    Cl->>AC: sim
    AC->>BD: status = cancelado · corretor_notificado = False
    Co->>AS: abre a Área do Corretor
    AS->>BD: listar_avisos_corretor
    AS-->>Co: ⚠️ cliente cancelou / remarcou / deu feedback / pediu avaliação

    Note over Cl,Co: Corretor cancela ou remarca
    Co->>AS: cancelar(agendamento)
    AS->>BD: status = cancelado
    AS->>Cl: mensagem no histórico do chat
    AS->>TG: avisar_cliente (se ele usa o Telegram)
    AS-->>Co: "cliente avisado por: chat + Telegram"
```

### 6.6 Captação: o cliente quer vender ou alugar o próprio imóvel

```mermaid
flowchart TD
    d["detectar_captacao<br/>'quero vender meu apartamento'"] --> f["pergunta, uma de cada vez:<br/>tipo · quartos · banheiros · vagas<br/>metragem · endereço · bairro · complemento"]
    f --> est["estimar_valor (domínio)<br/>comparáveis no bairro → vizinhos → zona<br/>+ R$/m² do bairro"]
    est --> msg["mostra a faixa estimada<br/>e explica que a avaliação<br/>presencial dá o valor certo"]
    msg --> agd["agenda tipo = avaliacao<br/>com o corretor da zona<br/>(ficha em 'detalhes')"]
    agd --> av["corretor vê 🏷️ na agenda<br/>e na aba Captações"]
    av --> r{"resultado da captação"}
    r -- "captado" --> pub["publica o imóvel na base<br/>(IM042 em diante)"]
    pub --> match["roda o match com<br/>clientes interessados"]
    r -- "pensando" --> fu["follow-up continua"]
    r -- "não captado" --> fimc(["encerra"])
```

### 6.7 Encerramento do atendimento

```mermaid
flowchart LR
    q["'pode encerrar' / botão<br/>/encerrar no Telegram"] --> r["EncerramentoAgent<br/>resumo do que foi combinado"]
    r --> n["pede uma nota de 1 a 5"]
    n --> s["salva a nota<br/>(métrica no Painel)"]
    q2["'não quero mais<br/>receber mensagens'"] --> nc["nao_contatar = True<br/>follow-up nunca mais escreve"]
```

---

## 7. Fluxos do corretor

O corretor não passa pelo grafo do cliente. Ele tem uma tela própria com 7 abas e um chat com um menu numerado (`MenuCorretorAgent`). Os dados vêm dos serviços; o LLM só redige.

```mermaid
flowchart LR
    m["'menu' / 'o que posso fazer?'"] --> menu["MenuCorretorAgent"]
    menu --> o1["1 📅 Minha agenda"] --> qa["ConsultaAgendaAgent"]
    menu --> o2["2 📝 Registrar resultado"] --> pv["PosVisitaService"]
    menu --> o3["3 🔎 Clientes sem visita"] --> cc["CarteiraCorretorService"]
    menu --> o4["4 💡 Sugestões p/ agendados"] --> cc
    menu --> o5["5 🏠 Imóveis novos × clientes"] --> mi["MatchImoveisService"]
    menu --> o6["6 📊 Meu desempenho"] --> ds["DesempenhoCorretorService"]
    menu --> o7["7 ✏️ Remarcar / cancelar"] --> ga["GestaoAgendaCorretorAgent"]
    menu --> o8["8 🏷️ Captações"] --> cs["CaptacaoService"]
    menu --> o9["9 🏘️ Imóveis da minha área"] --> cc
```

### 7.1 Do primeiro contato ao negócio fechado (funil)

O resultado da visita é o que alimenta o desempenho do corretor e o Painel do gestor.

```mermaid
flowchart LR
    lead["lead"] --> vis["visita confirmada"]
    vis --> res{"resultado"}
    res -- "👍 gostou" --> neg["em negociação"]
    res -- "📝 proposta + valor" --> neg
    neg --> fech["🤝 fechado + valor<br/>(conta em valor_fechado)"]
    res -- "🤝 fechado" --> fech
    res -- "👎 não gostou + motivo" --> perda["motivo registrado<br/>Preço · Localização · Tamanho..."]
    res -- "🚫 não compareceu" --> nc["não compareceu"]
    perda -- "aplicar_feedback" --> nova["próxima busca do cliente<br/>já considera o motivo"]
```

### 7.2 Imóvel novo encontra cliente (match)

```mermaid
flowchart LR
    novo["imóvel cadastrado<br/>(ou captado)"] --> comp["compativel(lead, imóvel)<br/>negócio · região · quartos · preço"]
    comp --> lista["clientes interessados"]
    lista --> corretor["corretor vê na aba<br/>'Imóveis novos'"]
    corretor --> av["avisar_cliente<br/>(chat + Telegram)"]
    av --> marca["lead.imoveis_avisados<br/>(não avisa duas vezes)"]
```

### 7.3 Painel do gestor

O `PainelGestorService.calcular(periodo_dias)` junta leads, agenda e eventos e devolve tudo pronto para as abas.

```mermaid
flowchart LR
    leads[("leads")] & agenda[("agenda")] & ev[("eventos")] --> pg["PainelGestorService"]
    pg --> k["KPIs<br/>leads · visitas · fechados · captações"]
    pg --> f["Funil"]
    pg --> at["Atenção agora<br/>o que pede ação hoje"]
    pg --> d["Demanda × oferta<br/>por bairro"]
    pg --> rk["Ranking de corretores"]
    pg --> ia["Métricas da IA<br/>notas 1–5 · encerrados"]
```

---

## 8. Rotinas automáticas

As rotinas rodam **dentro do bot do Telegram** (primeira verificação cerca de 20 s depois de subir; depois no intervalo configurado). Sem o bot, dá para disparar à mão com `python scripts/executar_followup.py` ou pelos comandos `/followup` e `/posvisita`.

### 8.1 Follow-up: quem recebe mensagem?

```mermaid
flowchart TD
    l["lead parado há mais de<br/>followup.minutos_sem_resposta"] --> h{"tem histórico?"}
    h -- "não" --> no(["não envia"])
    h -- "sim" --> nc{"pediu para não<br/>ser contatado?"}
    nc -- "sim" --> no
    nc -- "não" --> ag{"já tem agendamento<br/>(e não é captação)?"}
    ag -- "sim" --> no
    ag -- "não" --> p{"ficou negócio pela metade?<br/>compra · aluguel · investimento · captação"}
    p -- "não" --> e{"encerrou o atendimento?"}
    e -- "sim" --> no
    e -- "não" --> lim1{"já recebeu 2?"}
    lim1 -- "sim" --> no
    lim1 -- "não" --> re["mensagem de reengajamento"]
    p -- "sim" --> lim2{"já recebeu 3?"}
    lim2 -- "sim" --> no
    lim2 -- "não" --> arg["ArgumentosFollowUp<br/>fatos reais da base:<br/>imóveis, preço/m², rentabilidade"]
    arg --> ret["mensagem de retomada<br/>com argumentos"]
    re & ret --> env["envia pelo Telegram<br/>e grava no histórico"]
```

Mesmo que o cliente tenha encerrado a conversa, se ficou um negócio pela metade o Sr. Agim volta a procurá-lo, até 3 vezes, com argumentos tirados da base. Os fatos vêm do Python, e o LLM só os transforma numa mensagem convincente. O pedido de "não me mande mais mensagens" é sempre respeitado.

### 8.2 Pós-visita

```mermaid
sequenceDiagram
    participant Bot as Rotina (bot)
    participant PV as PosVisitaService
    participant Cl as Cliente
    participant AC as AgendaClienteAgent
    actor Co as Corretor

    Bot->>PV: enviar_pos_visita(horas_apos)
    PV->>Cl: "E aí, o que achou da visita?" [[POSVISITA:id]]
    Cl->>AC: "gostei, mas achei caro"
    AC->>AC: classificar_feedback → nao_gostou / Preço
    AC->>PV: guarda feedback no lead e no agendamento
    PV-->>Co: 💬 aviso na Área do Corretor
    Note over Cl: próxima busca já filtra por preço menor
```

---

## 9. Voz (Azure Speech)

A voz usa a **API REST** do Azure Speech (sem SDK nativo, que dava problema de instalação no Windows). Funciona no Telegram (mensagem de áudio) e no navegador (gravador do Streamlit).

```mermaid
sequenceDiagram
    actor C as Cliente
    participant UI as Telegram / Streamlit
    participant V as AzureSpeechService
    participant CS as ConversationService

    C->>UI: áudio (OGG no Telegram, WAV no navegador)
    UI->>V: fala_para_texto (WAV convertido p/ 16 kHz mono)
    V-->>UI: texto
    UI-->>C: 🎤 Entendi: "..."
    UI->>CS: processar_mensagem(texto)
    CS-->>UI: resposta
    UI-->>C: resposta em texto
    opt azure_speech.responder_em_audio = true
        UI->>V: texto_para_fala (pt-BR-AntonioNeural)
        V-->>UI: áudio
        UI-->>C: resposta falada
    end
```

Se algo falhar (chave, região, formato), `explicar_erro_de_voz` mostra o motivo em português e o cliente é convidado a escrever.

---

## 10. Dados e memória

```mermaid
erDiagram
    LEAD ||--o{ MENSAGEM : "histórico"
    LEAD ||--o{ AGENDAMENTO : "marca"
    CORRETOR ||--o{ AGENDAMENTO : "atende"
    IMOVEL ||--o{ AGENDAMENTO : "visitado em"
    CORRETOR }o--o{ ZONA : "atua em"
    IMOVEL }o--|| BAIRRO : "fica em"
    BAIRRO }o--|| ZONA : "pertence a"

    LEAD {
        string id
        string cpf
        string nome
        json perfil
        string temperatura
        json feedback_visitas
        json captacao
        bool atendimento_encerrado
        bool nao_contatar
    }
    AGENDAMENTO {
        string id
        string tipo "reuniao | visita | avaliacao"
        string status "sugerido | confirmado | cancelado"
        datetime data_hora
        string resultado_visita
        float valor_negociado
        string detalhes
    }
    IMOVEL {
        string id
        string tipo_negocio
        string bairro
        int quartos
        float preco
        float metragem
    }
    CORRETOR {
        string id
        string nome
        list zonas_atuacao
    }
```

| Onde | O que guarda |
|---|---|
| `data/agente_sdr.db` | leads (com histórico), agendamentos, eventos |
| `data/imoveis.db` | 41 imóveis (criado a partir de `imoveis.json` na primeira execução) |
| `data/corretores.json` | 9 corretores e suas zonas |
| `data/bairros_sp.json` | 59 bairros com zona e vizinhos |
| `data/mercado_investimento.json` | indicadores para a análise de investimento |
| `data/telegram_sessoes.json` | qual chat do Telegram é de qual lead |

**Duas memórias, dois papéis:** o `Lead` no SQLite é a memória de longo prazo e sobrevive a reinícios; o `EstadoConversa` é a memória de trabalho de uma única rodada.

---

## 11. Configuração e segurança

### De onde vem cada configuração

```mermaid
flowchart LR
    a["padrão no código"] --> b["config/settings.toml<br/>vai para o Git"]
    b --> c["config/settings.local.toml<br/>só desta máquina"]
    c --> d[".env / variáveis de ambiente<br/>SÓ segredos"]
    d --> s["Settings (imutável)"]
    s --> app["resto do app<br/>from src.config import settings"]

    b -. "segredo aqui?" .-> x["❌ app se recusa a iniciar"]
    c -. "segredo aqui?" .-> x
```

O último vence. As fontes são adaptadores (`ArquivoTomlFonte`, `AmbienteFonte`) atrás do protocolo `FonteConfiguracao`; acrescentar um Azure Key Vault, por exemplo, seria só mais uma fonte.

### Cuidados de segurança

- Chaves e tokens existem **só** no `.env`, que fica fora do Git. Cada campo de `Settings` declara se é segredo; segredo não aparece em `repr` nem em log.
- Um segredo encontrado num arquivo TOML faz o app **parar na inicialização**, para não ir parar no Git por engano.
- O CPF vai mascarado nos prompts enviados ao LLM.
- O cliente pode pedir para não ser mais contatado (`nao_contatar`).
- Sem chave, o modo mock permite demonstrar e desenvolver sem expor nada.

---

## 12. Observabilidade

```mermaid
flowchart LR
    cs["ConversationService"] -- "mensagem_recebida<br/>turno_processado<br/>agendamento_cancelado_pelo_cliente" --> ev["EventoStore<br/>(SQLite + logging)"]
    ag["Agenda · Pós-visita · Captação · Match"] -- "agendamento_cancelado_pelo_corretor<br/>resultado_visita_registrado<br/>captacao_resultado · imovel_cadastrado" --> ev
    rt["Rotinas automáticas"] -- "followup_disparado<br/>pos_visita_enviado" --> ev
    ev --> painel["Painel da Imobiliária"]
    ev --> log["terminal / arquivo de log"]
```

O Painel lê os eventos direto do banco, sem precisar interpretar arquivos de log.

---

## 13. Escalabilidade e caminho para produção

A POC roda numa instância só, e isso é consciente: SQLite local, sessões do Telegram num JSON e rotinas automáticas dentro do processo do bot. Como todas essas peças estão atrás de interfaces, a evolução é localizada:

```mermaid
flowchart LR
    subgraph hoje["Hoje (POC)"]
        h1["SQLite local"]
        h2["telegram_sessoes.json"]
        h3["rotinas dentro do bot"]
        h4["CRM simulado"]
        h5["RAG em memória"]
    end
    subgraph prod["Produção (Azure)"]
        p1["Azure SQL / Cosmos DB<br/>CPF indexado"]
        p2["sessões no banco"]
        p3["Azure Functions /<br/>Container Apps Jobs"]
        p4["CRM real via API"]
        p5["Azure AI Search"]
    end
    h1 --> p1
    h2 --> p2
    h3 --> p3
    h4 --> p4
    h5 --> p5
```

- A interface é *stateless*: todo o estado fica em `data/`. Com um banco gerenciado, dá para rodar várias réplicas atrás de um balanceador.
- `Dockerfile` e `docker-compose.yml` já permitem publicar em Azure Container Apps, App Service for Containers ou AKS.

---

## 14. Mapa de componentes

| Componente | O que faz | Usa LLM? |
|---|---|---|
| `IdentificacaoAgent` | Cadastra ou reconhece o cliente (nome + CPF) e mostra as agendas dele | não |
| `EncerramentoAgent` | Encerra o atendimento, pede a nota de 1 a 5, registra o opt-out | não |
| `AgendaClienteAgent` | Cliente vê, cancela e remarca; captura o feedback do pós-visita | não |
| `CaptacaoImovelAgent` | Cadastro do imóvel do cliente, estimativa de valor e avaliação agendada | não |
| `QualificadorAgent` | Extrai os dados da mensagem e classifica a temperatura do lead | sim |
| `EsclarecedorAgent` | Pergunta, com naturalidade, o que ainda falta | sim |
| `ConsultorImoveisAgent` | Busca (SQL + RAG) e apresenta imóveis e análise de investimento | sim |
| `DetalheImovelAgent` | Ficha completa de um imóvel, com fotos | sim |
| `AgendadorAgent` | Escolhe corretor por zona e carga, evita conflito, registra a visita | sim |
| `ResumidorAgent` | Resumo do lead para o corretor humano | sim |
| `RecapituladorAgent` | "O que já conversamos?" | sim |
| `MenuCorretorAgent` | Menu de 9 opções do corretor | só para redigir |
| `ConsultaAgendaAgent` / `GestaoAgendaCorretorAgent` | Corretor consulta / altera a agenda pelo chat | só para redigir |
| `FollowUpAgent` + `ArgumentosFollowUp` | Reengaja leads parados; negócio pendente recebe argumentos reais | sim |
| `ConversationService` | Orquestra uma rodada: estado → grafo → lead → CRM → eventos | — |
| `AgendaService` | Cancelar/remarcar e avisos cliente ↔ corretor | — |
| `PosVisitaService` / `CaptacaoService` | Resultados de visitas e captações (funil) | — |
| `MatchImoveisService` / `CarteiraCorretorService` | Imóvel novo × clientes / carteira e imóveis da área | — |
| `DesempenhoCorretorService` / `PainelGestorService` | Funil do corretor / visão do gestor | — |
| `AzureSpeechService` | Voz ↔ texto | — |
| `AppContainer` | Monta e injeta todas as dependências | — |
