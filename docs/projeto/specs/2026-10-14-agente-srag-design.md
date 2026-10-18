# Agente de Relatório SRAG — Desenho (spec)

> Certificação AI Engineer by Indicium AI · PoC "Indicium HealthCare Inc."
> Spec aprovado em conversa, seção a seção, em 14/10/2026. Autor: Breno Teodomiro.

## 1. Objetivo e critérios de sucesso

Um **agente** que gera, de forma automatizada, um relatório sobre Síndrome Respiratória Aguda Grave (SRAG)
com 4 métricas calculadas a partir da base real do Open DATASUS, 2 gráficos e comentários embasados em
**notícias em tempo real**.

**Entregáveis exigidos pelo desafio**

1. Repositório **público** no GitHub com a solução.
2. Documentação no README e/ou relatórios do repositório.
3. **PDF com o diagrama conceitual da arquitetura**: orquestrador, ferramentas, interações com LLM, banco
   de dados e fontes de notícias.

**Critérios de avaliação (10 pontos, mínimo 7):** escolha da arquitetura · governança e transparência
(auditoria e registro de decisões dos agentes) · guardrails · tratamento de dados sensíveis · clean code.

**Regra de escopo:** entra todo extra que reforça um dos 5 critérios. Fica de fora (documentado como
evolução futura) o que só agrega valor de negócio. Ver seção 10.

**Envio único:** depois de clicar em "Enviar" não há edição. Tudo é conferido antes do envio.

## 2. Os dados

- Arquivo: `INFLUD25_DATASUS-Versao26-06-2025.zip` → CSV `;`, UTF-8 (medido no arquivo; o plano previa latin-1). Colunas e linhas: `make qualidade`.
- Período de `DT_SIN_PRI`: 29/12/2024 a 25/06/2025.
- `NU_NOTIFIC` é único (sem duplicados). Nenhum caso com sintoma após a notificação.
- Idade em `NU_IDADE_N` + `TP_IDADE` (1 = dias, 2 = meses, 3 = anos).
- Contagens **não** ficam neste documento: são geradas por `make qualidade` (ver 6.4).

## 3. Arquitetura escolhida (abordagem C — híbrida)

Foram avaliadas três abordagens:

| | Abordagem | Veredito |
|---|---|---|
| A | Pipeline fixo, LLM só redige | Descartada: não há agente nem decisão de ferramenta |
| B | Agente com text-to-SQL livre | Descartada: o LLM poderia ler colunas sensíveis e inventar cálculo |
| **C** | **Orquestrador LangGraph com ferramentas fechadas + guardrails + auditoria** | **Escolhida** |

Princípios:

1. **Números nunca vêm do LLM.** Métricas e séries saem de SQL determinístico. O LLM decide *o que*
   consultar e *escreve o comentário*. Todo número que ele cita é conferido.
2. **Um orquestrador, não vários agentes.** É mais fácil de auditar, faz menos chamadas e custa menos. A
   evolução para multiagente fica documentada como ponto de encaixe.
3. **Privilégio mínimo.** O agente só enxerga agregados.

### 3.1 Fluxo (LangGraph `StateGraph`)

```
entrada (uf?, pergunta_extra?)
 → [1] guardrail_entrada        recusa registrada se fora do escopo / injeção
 → [2] orquestrador (LLM + tools) ◄────────────┐
 → [3] ferramentas (auditadas)                  │
 → [4] verificar_cobertura (código) ─ faltou? ──┘  (máx. 2 voltas)
 → [5] redator (LLM, saída estruturada Pydantic)
 → [6] guardrail_saida           falhou → volta 1x ao redator → falhou de novo → faixa "revisão humana", texto retido
 → [7] montar_relatorio (código) gráficos + HTML + Markdown + anexo de auditoria
```

`verificar_cobertura` exige: as 4 métricas, as 2 séries, ≥1 notícia do período dos dados e ≥1 do cenário
atual (ou registro de que a busca não retornou nada).

### 3.2 Ferramentas

Todas recebem parâmetros validados por Pydantic e um campo obrigatório **`motivo: str`**, em que o LLM
explica por que está chamando. O motivo vai para a auditoria. Nenhuma ferramenta aceita SQL.

| Ferramenta | Parâmetros | Retorno |
|---|---|---|
| `consultar_metricas` | `metrica` ∈ {aumento_casos, mortalidade, uti, vacinacao}, `uf?` | valor, numerador, denominador, excluídos, período, definição, limitação |
| `consultar_serie` | `tipo` ∈ {diaria_30d, mensal_12m}, `uf?` | pontos + flags `em_consolidacao` / `fora_da_base` / `parcial` |
| `consultar_distribuicao` | `dimensao` ∈ {classificacao, faixa_etaria}, `uf?` | casos e percentual por categoria |
| `buscar_noticias` | `termo`, `janela` ∈ {periodo_dos_dados, cenario_atual} | título, fonte, data, url, trecho (≤500 car.) marcado como externo |
| `consultar_dicionario` | `pergunta` | trechos do dicionário + página de origem (RAG pgvector) |

### 3.3 Notícias em duas janelas

Os dados terminam em jun/2025 e o "tempo real" é a data da execução. O relatório separa as duas coisas:

- **Contexto do período dos dados:** notícias de 30 dias antes da data de referência da base até ela.
  Embasam os números.
- **Cenário atual:** notícias dos últimos 30 dias a partir da execução. Atendem ao "tempo real".

Regra do prompt, conferida pelo guardrail de saída: notícia do cenário atual **nunca** explica número do
período dos dados.

Fonte: Tavily (`topic=news`, `start_date`/`end_date`). Reserva automática: Google News RSS (sem chave),
com os operadores `after:`/`before:` na consulta e filtro de data aplicado de novo no código.

## 4. Métricas (catálogo)

**"Hoje" = data máxima de `DT_SIN_PRI` na base** (data de referência). Data de cada caso = `DT_SIN_PRI`.
**Janela de consolidação = 14 dias** (atraso de notificação). Cada métrica devolve numerador, denominador
e excluídos (vazio/ignorado).

| Métrica | Definição | Limitação declarada no relatório |
|---|---|---|
| Taxa de aumento de casos | (casos nos 7 dias consolidados mais recentes − 7 dias anteriores) ÷ 7 dias anteriores. Consolidado = até `ref − 14d`. Apoio: 30 vs 30 dias | Dias recentes ainda recebem notificações |
| Taxa de mortalidade | `EVOLUCAO=2` ÷ `EVOLUCAO ∈ {1,2,3}` | Casos sem desfecho ficam fora do denominador |
| Taxa de UTI | `UTI=1` ÷ `UTI ∈ {1,2}` | **Não é ocupação de leitos**: a base não tem capacidade. É a proporção de casos de SRAG internados em UTI. Ocupação real exige CNES (evolução futura) |
| Taxa de vacinação | `VACINA_COV=1` ÷ `VACINA_COV ∈ {1,2}`. Ao lado: gripe (`VACINA`) | É vacinação **entre os internados**, não da população |

Mortalidade, UTI e vacinação são calculadas sobre **todo o período da base** (temporada 2025 até a data
de referência). Em janelas curtas, a mortalidade ficaria enviesada, porque os casos recentes ainda não
têm desfecho.

Quebra complementar: casos por `CLASSI_FIN` e por faixa etária (influenza, outro vírus, outro agente, não especificado,
COVID-19).

**Gráficos**

- Diário, 30 dias até a data de referência. Barras dos últimos 14 dias em tom claro, com legenda "em
  consolidação".
- Mensal, 12 meses até a data de referência. Meses anteriores ao início da base aparecem vazios com a nota
  "fora do período da base".

O catálogo é **declarativo** (`metricas/catalogo.py`: nome, view, definição, limitação). Uma métrica nova
= uma view nova + uma entrada nova.

## 5. Camadas de dados e dados sensíveis

Dado de saúde, raça e etnia é **dado pessoal sensível** (LGPD art. 5º, II). A base é pública e sem nome,
mas a combinação de nascimento + município + sexo + datas permite reidentificar. O projeto trata tudo como
sensível.

| Schema | Conteúdo | Acesso |
|---|---|---|
| `raw` | CSV como veio: todas as colunas, tudo `text`, carga via `COPY` | `srag_etl` |
| `silver` | colunas tipadas e limpas (lista em `docs/DICIONARIO_SILVER.md`), chave interna no lugar de `NU_NOTIFIC`, idade → faixa etária | `srag_etl` |
| `gold` | **somente views agregadas** + a tabela `dicionario_trechos` (texto do dicionário público, para o RAG) | `srag_agente` (SELECT) |
| `auditoria` | execuções e eventos do agente | `srag_auditor` (INSERT/SELECT; UPDATE só nas colunas de encerramento de `execucoes` e só enquanto `em_andamento`; eventos não se alteram), usado só pelo registro de auditoria, nunca pelas ferramentas do LLM |

**Removidos na passagem raw → silver (minimização, LGPD art. 6º, III):** `NU_NOTIFIC`, `DT_NASC`,
município (notificação, residência e internação), `CS_RACA`, `CS_ETINIA`, `CS_ESCOL_N`, `PAC_COCBO`,
`PAC_DSCBO`, `NM_UN_INTE` e códigos de unidade, lotes de vacina, `TEM_CPF`, `POV_CT`, `ESTRANG`, campos
de texto livre (`*_DES`, `*_OUT`, `MORB_DESC`), sexo e sintomas (nenhuma métrica usa). Fica: UF de
notificação, faixa etária, datas de notificação/sintomas/internação/UTI/evolução, `HOSPITAL`, `UTI`,
`SUPORT_VEN`, `EVOLUCAO`, `CLASSI_FIN`, `VACINA`, `VACINA_COV`. A lista final, com o motivo de cada coluna, fica em `docs/DICIONARIO_SILVER.md`.

**Proteções, cada uma com teste automatizado**

1. `srag_agente`: `SELECT` só em `gold`, `default_transaction_read_only=on`, `statement_timeout=5s`.
   Um teste tenta ler `silver` e `raw` com esse usuário e **precisa falhar**.
2. Supressão de células pequenas: contagem de 1 a 4 é devolvida como `"<5"` (zero não se suprime).
   Havendo algum `"<5"` numa resposta, a menor célula com 5 ou mais sai `"oculto"` (supressão
   complementar; percentual nulo; empate: a primeira), e outras saem enquanto algum `"<5"` ficar
   determinável; o total é exato. Na série diária a regra roda por bloco das janelas cuja soma
   também é publicada (semanas do aumento, dias em consolidação, meses inteiros nos 30 dias).
   Regra e limites: `docs/adr/0011-supressao-de-celulas-pequenas.md`.
3. A auditoria guarda só agregados, nunca linhas da base.
4. `.env` fora do Git, `.env.example` versionado, `gitleaks` no pre-commit e no CI.
5. CSV e zip fora do Git. O README explica a origem e onde colocar o arquivo. O dicionário de dados
   (documento público do DATASUS, usado pelo RAG) fica versionado em `dados/referencia/`. O PDF do
   enunciado do desafio não é versionado.

### 5.1 Qualidade (silver)

- `""` e `9` → categoria "ignorado", sem apagar a linha.
- Data: aceita o prefixo `AAAA-MM-DD` (há valores com hora e nanossegundos). Fora de
  [mín(`DT_NOTIFIC` plausível) − 365 dias, máx(`DT_NOTIFIC` plausível)] → `NULL` (a base tem
  internações nos anos 1695 e 2202).
- `DT_NOTIFIC` plausível = a até 365 dias da mediana das `DT_NOTIFIC` válidas. Assim uma única
  notificação absurda não alarga a janela nem move o "hoje". As descartadas do cálculo são
  contadas em `silver.qualidade`. Sem notificação válida, toda data vira `NULL`.
- Idade em anos completos: `TP_IDADE` 1 (dias) ÷ 365 e 2 (meses) ÷ 12, por divisão inteira (a base
  tem idade em meses acima de 11). Fora de 0–120 anos → `NULL`. UF fora das 27 → `NULL`.
- `EVOLUCAO` vazia = "sem desfecho" (caso em aberto), separada de `9` = "ignorado".
- `CLASSI_FIN` vazia = `em_investigacao` (caso ainda sem classificação final), separada de `9` = "ignorado".
- Cada regra registra quantas linhas afetou em `silver.qualidade`.
- `make qualidade` gera `docs/QUALIDADE_DADOS.md`. Número nunca é escrito à mão.

## 6. Guardrails

Princípio: determinístico sempre que der, LLM só onde regra não resolve. Sem framework pronto (NeMo,
Guardrails AI): código próprio é mais testável e explicável.

| Camada | Bloqueia | Mecanismo | Ação |
|---|---|---|---|
| 1. Entrada | fora do escopo, injeção, texto > 500 car. | tamanho + regex de injeção + classificador LLM `{no_escopo, motivo}` | recusa registrada |
| 2. Ferramentas | parâmetro inválido, SQL malicioso, consulta pesada | Pydantic + SQL fixo com bind + usuário read-only + timeout | erro ao orquestrador |
| 3. Conteúdo externo | injeção indireta, HTML/script | limpa HTML, corta 500 car., envolve em `<noticia_externa>`, descarta item com padrão de injeção, prioriza fontes confiáveis | descarte registrado |
| 4. Saída | número não conferido, link inventado, notícia fora da janela, CPF/CNS/telefone/e-mail, recomendação clínica | conferência numérica com tolerância de arredondamento, URL ∈ resultados, data × seção, regex PII, lista de termos clínicos | até 3 tentativas (a nova tentativa recebe instruções específicas por violação, com o valor exato das evidências) → faixa "revisão humana necessária"; texto reprovado é retido (fora do relatório e da trilha, que guarda violações, tamanho e hash); dado pessoal no texto é mascarado (não conta como violação) |
| 5. Execução | laço, custo, indisponibilidade | ≤12 chamadas de ferramenta, teto de tokens/tempo, temperatura 0,2, retry com backoff, reserva OpenRouter→OpenAI e Tavily→RSS | para, registra, entrega parcial com aviso |

Aviso fixo, inserido pelo código: *"Prova de conceito. Não substitui avaliação clínica nem boletim
epidemiológico oficial."*

`tests/guardrails/` tem casos de ataque (notícia com injeção, número errado, link inventado, CPF, pergunta
fora do escopo). O README mostra a tabela "ataque → resultado".

## 7. Governança e transparência

- `auditoria.execucoes`: id, início/fim, status, provedor/modelo, tokens, custo estimado (USD), parâmetros,
  `git sha`, hash dos prompts, data de referência e contagem da silver.
- `auditoria.eventos`: id_execucao, sequência, timestamp, nó, tipo (`llm`, `ferramenta`, `guardrail`,
  `decisao`), ferramenta, argumentos, **motivo**, resumo do resultado, duração, tokens, erro.
- Cada relatório gera `relatorios/<data>_<uf>_<id8>/auditoria.jsonl` e um anexo **"Como este relatório foi
  gerado"**: ferramentas chamadas e motivos, guardrails aprovados/reprovados, modelo, custo.
- Prompts versionados em arquivos (`agente/prompts/*.md`), com hash registrado em cada execução.
- Langfuse **opcional**: liga sozinho se as chaves estiverem no `.env`.
- Decisões em `docs/adr/` (ADRs curtos): arquitetura C, métrica de UTI, corte de 14 dias, remoção do
  município, guardrails próprios, embeddings locais, notícias em duas janelas.

## 8. Stack

| Camada | Escolha | Motivo |
|---|---|---|
| Linguagem/ambiente | Python 3.12 + `uv` | já instalado, lock reproduzível |
| Banco | Postgres 16 + pgvector (`pgvector/pgvector:pg16`) no Docker Compose, porta **5435** | um comando sobe tudo. 5434 já está ocupada na máquina |
| Carga e transformação | `psycopg` (`COPY`) + arquivos `.sql` versionados e numerados | transparente, sem dbt (excessivo para 1 tabela) |
| Agente | LangGraph 1.x + `langchain-openai` | grafo explícito e auditável |
| LLM | OpenRouter `openai/gpt-4.1-mini` (padrão) · OpenAI (reserva) | configurável no `.env` (`LLM_PROVIDER`, `LLM_MODELO`) |
| Notícias | API da Tavily via `httpx` + `feedparser` (RSS) | controle de bytes, timeout e erro, testável sem rede + reserva sem chave (ADR 0009) |
| RAG | `pymupdf` (PDF) + `fastembed` (ONNX) multilíngue local + pgvector, busca híbrida (nome exato do campo + similaridade) | sem custo nem chave, reproduzível pelo avaliador (ADR 0006) |
| Relatório | `matplotlib` + `Jinja2` → HTML autocontido + Markdown | abre em qualquer navegador |
| Diagrama | Mermaid (`docs/arquitetura.mmd`) → PDF via `mermaid-cli` no Docker | fonte versionada + PDF exigido |
| Qualidade | `ruff`, `pytest`, `pytest-cov`, `pre-commit`, `gitleaks`, GitHub Actions | clean code verificável |

### 8.1 Estrutura do repositório

```
src/srag_agente/
  config.py                  # pydantic-settings, lê .env
  dados/                     # carga_raw.py, transformar.py, sql/00_estrutura.sql … 04_auditoria.sql
  metricas/catalogo.py
  ferramentas/               # registro.py (as ferramentas fechadas), noticias.py, dicionario.py
  rag/indexar.py
  agente/                    # estado.py, grafo.py, nos.py, prompts/
  guardrails/                # entrada.py, conteudo_externo.py, saida.py, limites.py
  auditoria/registro.py
  relatorio/                 # graficos.py, montar.py, templates/
  nucleo.py                  # gerar_relatorio() — usado pela CLI e por uma futura tela
  cli.py
tests/ (unit, guardrails, integracao, e2e)
docs/ (adr/, arquitetura.mmd, arquitetura.pdf, DICIONARIO_SILVER.md, QUALIDADE_DADOS.md)
relatorios/exemplo/          # relatório real gerado, versionado para o avaliador ver sem rodar
docker-compose.yml · Makefile · pyproject.toml · .env.example · README.md
```

Comandos: `make subir`, `make carga`, `make indexar`, `make relatorio`, `make testes`, `make qualidade`,
`make diagrama`.

## 9. Testes

- **Unitários:** catálogo de métricas, validações Pydantic, guardrails (todas as regras), sanitização de
  notícia, montagem do relatório.
- **Integração (Postgres do Compose, banco `srag_teste`):** CSV sintético com ~200 linhas e resultados
  esperados calculados à mão para cada métrica e série. Testes de permissão do `srag_agente`.
- **Agente:** LLM falso com chamadas de ferramenta roteirizadas (sem custo), cobrindo o caminho feliz,
  falta de cobertura, falha de guardrail e reserva de provedor.
- **E2E** (`-m e2e`, manual, com chaves reais): gera o relatório de exemplo.
- O CI roda lint + unit + integração com Postgres como serviço.

## 10. Fora do escopo (documentado como evolução futura)

| Item | Ponto de encaixe |
|---|---|
| Tela Streamlit | chama `nucleo.gerar_relatorio()`. Retrabalho zero. Fazer só se sobrar tempo antes do envio |
| Ocupação real de UTI (CNES) | carga nova + view + entrada no catálogo |
| Multiagente | orquestrador chamaria subgrafos; ferramentas inalteradas |
| Previsão de casos, deploy em nuvem | ferramenta + view nova; Compose já pronto |

## 11. Pré-requisitos que dependem do Breno

- Chave da OpenRouter (já existe) e chave da Tavily (criar conta gratuita em tavily.com) no `.env`.
- OK final antes de tornar o repositório público e antes de clicar em "Enviar" na plataforma.
