# Decisões de arquitetura (ADRs)

Cada arquivo registra uma decisão: o contexto, o que foi decidido, as alternativas, as
consequências e os limites conhecidos. Decisão tomada não se reabre em silêncio: se mudar, nasce
um ADR novo que diz o que mudou e qual ele substitui.

Regra destes documentos: **nenhum número medido** (contagem de testes, cobertura, tempo, acerto
do RAG). Onde há medida, o ADR aponta o comando que mede (`make testes`, `make qualidade`,
`make avaliar-rag`).

| ADR | Decisão | Critério de avaliação que reforça |
|---|---|---|
| [0001](0001-arquitetura-orquestrador-ferramentas-fechadas.md) | Orquestrador LangGraph com ferramentas fechadas, sem SQL livre, `motivo` obrigatório | arquitetura · guardrails |
| [0002](0002-uti-como-proporcao-de-casos.md) | "UTI" = proporção de casos internados em UTI, não ocupação de leitos | transparência |
| [0003](0003-numeros-do-sql-referencia-e-consolidacao.md) | Números só do SQL da gold; "hoje" = máx. `DT_SIN_PRI`; consolidação de 14 dias | arquitetura · transparência |
| [0004](0004-silver-sem-colunas-sensiveis.md) | Silver sem município, nascimento, raça/etnia, ocupação, sexo e textos livres | dados sensíveis |
| [0005](0005-guardrails-proprios-em-camadas.md) | Guardrails próprios e determinísticos em camadas; HTML autocontido com CSP por hash | guardrails |
| [0006](0006-embeddings-locais-onnx.md) | Embeddings locais (fastembed/ONNX) no pgvector, com busca híbrida por nome de campo | arquitetura · custo |
| [0007](0007-noticias-em-duas-janelas.md) | Notícias em duas janelas: período dos dados × cenário atual | transparência · guardrails |
| [0008](0008-metricas-acumuladas-no-periodo.md) | Mortalidade, UTI e vacinação sobre todo o período da base | transparência |
| [0009](0009-noticias-via-httpx-tavily-rss.md) | Notícias por `httpx` direto (Tavily → Google News RSS), não `langchain-tavily` | guardrails · clean code |
| [0010](0010-papeis-de-banco-e-trilha-de-auditoria.md) | Papéis de banco com privilégio mínimo e trilha de auditoria Postgres + JSONL | governança · dados sensíveis |
| [0011](0011-supressao-de-celulas-pequenas.md) | Supressão `<5`, complementar `oculto` e por janelas publicadas, com limites declarados | dados sensíveis |
| [0012](0012-demo-online-evolucao-prevista.md) | **Substituída pelo 0013.** Demonstração online (Streamlit Community Cloud + Postgres gerenciado) ficava como evolução prevista: desenho, riscos e critérios, sem implementação | arquitetura · dados sensíveis · governança |
| [0013](0013-demonstracao-online.md) | Demonstração online opcional (`DEMO_ONLINE=1`): só agregados da gold no banco gerenciado, pacote CSV com lista fechada de colunas, carga com travas contra o banco errado, teto diário na tela e no banco, pergunta complementar desligada, busca do dicionário sem modelo | arquitetura · governança · guardrails · dados sensíveis |

Fonte da verdade do desenho: [`docs/projeto/specs/2026-10-14-agente-srag-design.md`](../projeto/specs/2026-10-14-agente-srag-design.md).
