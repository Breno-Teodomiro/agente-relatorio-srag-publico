# ADR 0013 — Demonstração online com agregados da gold e teto diário no banco

- **Status:** aceita e implementada (18/10/2026); substitui o [ADR 0012](0012-demo-online-evolucao-prevista.md)

## Contexto

O ADR 0012 deixou a demonstração online como evolução prevista e listou o que precisava estar
pronto antes de publicá-la: teto global diário testado com duas sessões simultâneas, limite de gasto
na chave do provedor de LLM, memória medida, um ADR que substitua a regra "a tela só roda em
`127.0.0.1`", banco gerenciado com os mesmos papéis e conferência de que nenhum dado de `raw` ou
`silver` sai da máquina local.

O caminho local continua sendo o padrão do projeto: `make tudo` não muda e não depende de nada
desta decisão.

## Decisão

**A demonstração é opcional e só existe com `DEMO_ONLINE=1`.** Sem essa configuração, a tela
continua presa a `127.0.0.1` e o banco local continua igual.

| Peça | Escolha |
|---|---|
| Aplicação | Streamlit Community Cloud, entrada `demo/streamlit_app.py` (põe `src/` no caminho de importação, sem instalar o pacote), dependências em `demo/requirements.txt` gerado do `uv.lock` por `make demo-requisitos`; o CI confere que o arquivo está em dia |
| Banco | Postgres gerenciado gratuito (Neon) com `pgvector`, conexão **direta** (host sem `-pooler`) e TLS obrigatório |
| Conexão | as mesmas variáveis do projeto (`PG_HOST`, `PG_PORTA`, `PG_BANCO`, senha por papel), mais `PG_SSLMODE`, `PG_SSLROOTCERT`, `PG_CONNECT_TIMEOUT` e `PG_USUARIO_ADMIN`. Não há URL de conexão: o usuário de cada papel continua fixado no código, e uma credencial colada no lugar errado não troca o papel. Sem os campos novos, a conexão local é a de sempre |
| Dados na nuvem | só as tabelas `gold.referencia`, `casos_diarios`, `indicadores`, `janelas_aumento`, `distribuicao` (o resultado das views locais, com os mesmos nomes), `gold.dicionario_trechos`, `gold.pacote_demo` (proveniência) e o esquema `auditoria`. Nenhum objeto de `raw` ou `silver` |
| Pacote | `uv run srag demo exportar [--saida demo/pacote]` lê a gold local **com o papel do agente** (que não alcança `raw`, `silver` nem `auditoria`), num retrato só (`REPEATABLE READ READ ONLY`), e grava um CSV por tabela com lista fechada de colunas (`src/srag_agente/demo/__init__.py`) e um `manifesto.json` com o sha256 de cada arquivo. Coluna nova na gold faz a exportação falhar; nome de coluna proibido (regra do ADR 0004) ou valor fora do domínio esperado reprova o pacote. O pacote não vai para o Git |
| Carga | `uv run srag demo carregar --env-arquivo .env.demo --alvo <host> [--pacote demo/pacote] [--senha-em-texto] [--trocar-senhas]`. O `--env-arquivo` é obrigatório (o `.env` local nunca é a fonte) e o `--alvo` tem de ser igual ao `PG_HOST` do arquivo. Recusa host local, TLS fraco, banco que tenha `raw` ou `silver` e tabela da gold com colunas diferentes da lista, sem apagar nada. O pacote é conferido de novo (sha256, colunas, dado pessoal). Tudo roda numa transação do dono do banco e, antes do `COMMIT`, confere linhas por tabela, catálogo e atributos dos papéis. Nada do pacote é executado como SQL: o DDL é fixo no código (`src/srag_agente/demo/sql/`) e os dados entram por `COPY` |
| Papéis | `srag_agente` e `srag_auditor` criados **por SQL** pela carga (no Neon, papel criado pelo painel herda privilégios de administrador), com os mesmos privilégios do ADR 0010; o papel de carga local (`srag_etl`) não existe no banco gerenciado. A senha só é enviada quando o papel não existe ou com `--trocar-senhas`. A carga confere que nenhum dos dois tem poder de administrador nem participa de outro papel |
| Prova de permissões | `uv run srag demo conferir --env-arquivo .env.demo --alvo <host>` tenta, com as credenciais da tela, o que cada papel não pode fazer e confere a recusa do banco, sem escrever nada. O mesmo código roda nos testes de integração contra um banco local cujo dono não é superusuário |
| Teto diário | limites em `auditoria.limites_demo`, semeados por SQL versionado (`src/srag_agente/demo/sql/02_teto_demo.sql`: mudar o limite é mudar o arquivo e carregar de novo). Valores iniciais: 10 relatórios, 40 buscas de notícia e US$ 1,00 de gasto estimado por dia (política, não medida). A tela confere os três dentro da trava global, antes de gerar, com o dia contado de meia-noite a meia-noite no horário de Brasília pelo relógio do banco. Um gatilho no banco da demonstração recusa a execução além do limite de relatórios mesmo com dois processos (trava consultiva de transação). A recusa acontece antes de qualquer chamada ao modelo |
| Gasto | além do teto diário, limite de gasto configurado na chave do provedor de LLM dedicada à demonstração. A tela recusa abrir com `OPENAI_API_KEY` presente: a reserva automática escaparia do limite da chave principal |
| Pergunta complementar | **desligada na demonstração.** Texto livre de visitante anônimo não vai ao modelo externo nem à trilha; a tela explica o motivo no lugar do campo. Localmente, continua disponível |
| Busca do dicionário | `BUSCA_DICIONARIO=texto` na demonstração: nome exato e busca de texto completo do Postgres, sem carregar o modelo de embeddings. O padrão local continua `vetor` |
| Segredos | só nos *secrets* da plataforma (agente, auditor, chave de LLM e, opcional, da Tavily) e no `.env.demo` local de quem carrega (que tem também a senha do dono). A tela lê os *secrets* e repassa ao `Config` só as chaves que são campos dele |
| Proveniência | cada carga grava sha256 do pacote, versão do código que o exportou e data de referência em `gold.pacote_demo`; o rodapé da tela mostra o pacote e o modo da busca |

**A regra "a tela só roda em `127.0.0.1`" passa a valer só fora do modo demonstração.** No modo
demonstração, a tela confere na partida e **não abre** (mensagem genérica ao visitante; o motivo vai
só para o log do servidor) se qualquer um destes falhar:

1. `PG_HOST` remoto e `PG_SSLMODE` em `require`, `verify-ca` ou `verify-full`;
2. nenhuma credencial de dono nem de carga (`PG_SENHA_ADMIN`, `PG_SENHA_ETL`), nenhuma
   `OPENAI_API_KEY`, nenhuma chave do Langfuse; provedor OpenRouter com chave definida;
3. opções efetivas do Streamlit: sem detalhe de erro, proteção XSRF e CORS ligadas, barra de
   ferramentas só de visualização, sem estatística de uso, sem arquivos estáticos, sem recarga de
   código;
4. o banco tem o gatilho do teto e a linha de limites.

Valem também: teto diário na tela e no banco, teto por sessão (`MAX_RELATORIOS_SESSAO`), limite de
gasto na chave, mensagens de erro sem host, usuário nem SQL, e as mesmas guardrails e auditoria da
CLI.

## Alternativas consideradas

- **URL de conexão por papel (`DATABASE_URL`, desenho do ADR 0012):** descartada. O usuário viria da
  configuração, e não do código: a URL do dono colada no lugar da do agente faria o agente rodar como
  dono sem que nada percebesse.
- **Pooler transacional do provedor:** desnecessário no volume da demonstração (um relatório por
  vez). Fica como plano B, desligando os *prepared statements* automáticos do driver.
- **Pacote em SQL (`pg_dump` ou `INSERT`):** descartado; o arquivo seria executado como SQL no banco
  gerenciado. CSV com DDL fixo no código não executa nada que venha do pacote.
- **Teto só na tela:** não segura dois processos. **Teto só no banco:** a mensagem ao visitante viria
  como erro. Ficam os dois.
- **Modelo de embeddings na demonstração:** adiado até medir a memória na própria plataforma; o
  processo com o modelo carregado passa do mínimo de memória que a plataforma publica.
- **Pergunta complementar ligada, sem guardar o texto:** possível, mas exige remover a pergunta de
  todo campo derivado da trilha, e a paráfrase feita pelo modelo escapa da remoção exata. Para a
  primeira publicação, desligada.
- **Limite por IP:** descartado. Atrás do proxy da plataforma o IP do visitante não é confiável, é
  falsificável por cabeçalho e é dado pessoal.

## Consequências

- Quem avalia pode ver o agente sem instalar nada; quem quer reproduzir continua com `make tudo`, e
  quem quer a própria demonstração segue [`docs/demo-online.md`](../demo-online.md).
- A demonstração mostra os números do último pacote carregado (`gold.pacote_demo`), não da base
  local de quem roda.
- A busca do dicionário na demonstração é menos precisa que a local em parte das perguntas em
  linguagem natural.
- A demonstração não mostra a pergunta complementar; os guardrails de entrada continuam provados
  pelos testes e pela tabela "Ataque → resultado" do README.
- No banco da demonstração, a senha dos papéis vai em texto sobre TLS (o provedor não aceita hash);
  na carga local continua em hash SCRAM.

## Limites conhecidos

- O banco gerenciado não aceita senha em hash: a senha dos papéis vai em texto, sobre TLS, só na
  criação ou com `--trocar-senhas`. O texto do `CREATE ROLE` pode ficar na estatística de consultas
  do servidor, visível para quem administra o projeto no provedor.
- O gatilho conta relatórios; buscas e gasto são conferidos pela tela, que roda num processo só. O
  gasto do dia é estimado pelo preço configurado, e a chamada que falha sem resposta não entra.
- O teto por sessão é contornável recarregando a página; quem segura o total é o teto diário.
- Um abuso esgota o teto do dia, e a demonstração fica sem gerar relatório até a meia-noite (o
  dicionário e o relatório de exemplo do repositório continuam disponíveis).
- Contagens pequenas continuam legíveis para quem tiver a credencial do agente (ADR 0010, limite 3).
- A trilha de auditoria no banco gerenciado não tem limite de tamanho por evento; o crescimento por
  dia fica limitado pelo teto de relatórios. Não há purga automática.
- Hibernação do app e do banco gratuitos: a primeira visita depois de um período sem uso demora.

## Pendente de confirmação no primeiro deploy

Afirmações sobre a plataforma que a documentação dela não garante. Cada uma tem como conferir e o
que fazer se não valer.

| Ponto | Como conferir | Se não valer |
|---|---|---|
| A plataforma usa `demo/requirements.txt` (procurado primeiro na pasta da entrada) e instala a versão fixada do Streamlit | log de build do app | último recurso: fixar o Streamlit na lista principal do `pyproject.toml` |
| A plataforma respeita ou sobrescreve `server.address = "127.0.0.1"` do `.streamlit/config.toml` | o app abre ou não; log de execução | tirar `address` do `config.toml` (o `make app` já passa `--server.address 127.0.0.1`, e a tela local continua recusando outro endereço) |
| Memória real do processo | linha `memória (partida)` e `memória (depois do relatório)` no log do app, com RSS e limite do contêiner | se até o modo `texto` passar do limite, esconder a aba Dicionário na demonstração |
| `git` existe no contêiner (versão do código gravada na auditoria) | coluna `versao_codigo` da execução em `auditoria.execucoes` | aceitar `desconhecida` ou fornecer `SRAG_VERSAO_CODIGO` (depende de a plataforma expor *secrets* como variável de ambiente, também não confirmado) |
| `verify-full` com `sslrootcert=system` acha os certificados do sistema | a carga conecta | `PG_SSLMODE=require` (cifrado, sem verificar o servidor) |
| O dono do banco gerenciado pode criar a extensão `vector` e rodar os `REVOKE`/`ALTER ROLE` da carga | a carga termina sem erro | rodar o comando recusado no editor SQL do provedor como dono e registrar |

## Riscos aceitos

Revisão na publicação e a cada 30 dias enquanto a demonstração estiver no ar. As chaves dedicadas à
demonstração são revogadas ao fim do período de avaliação.

| Risco | Por que se aceita |
|---|---|
| Abusador esgota o teto do dia | o dano máximo é o valor do teto |
| Sem limite por IP | IP não é confiável atrás do proxy e é dado pessoal |
| Células pequenas legíveis com a credencial do agente | o microdado de origem é público; a barreira é a credencial, que só existe nos *secrets* |
| Demonstração embutida em site de terceiro | o teto diário limita o custo |
| Hibernação | custo zero; o aviso do topo da tela diz que a primeira visita pode demorar |

## Como conferir

- `make testes`: inclui a exportação, a carga, as permissões e o gatilho contra um banco local da
  demonstração cujo dono não é superusuário.
- `make demo-conferir ALVO=<host>` contra o banco gerenciado: todas as linhas `ok`.
- `uv run python -m srag_agente.rag.avaliar --modo ambos` mede a distância entre as duas buscas do
  dicionário (rodar logo depois de `make indexar`).
- Memória: linhas de memória no log da aplicação publicada.
- Uso do dia, no editor SQL do provedor:
  `SELECT count(*), sum(custo_usd) FROM auditoria.execucoes WHERE inicio >= (date_trunc('day', now() AT TIME ZONE 'America/Sao_Paulo') AT TIME ZONE 'America/Sao_Paulo');`
- Tamanho da trilha:
  `SELECT pg_size_pretty(pg_total_relation_size('auditoria.eventos') + pg_total_relation_size('auditoria.execucoes'));`
