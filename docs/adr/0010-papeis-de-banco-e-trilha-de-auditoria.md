# ADR 0010 — Papéis de banco com privilégio mínimo e trilha de auditoria Postgres + JSONL

- **Status:** aceita (14/10/2026, spec §5 e §7; endurecida nas Tasks 2, 4 e 10)

## Contexto

O agente roda código dirigido por um LLM. Se ele tiver acesso amplo ao banco, um erro ou uma
injeção vira leitura de dado sensível. E a avaliação pede governança: saber, depois, o que o
agente decidiu, com qual modelo, código e prompt.

## Decisão

**Papéis** (`src/srag_agente/dados/sql/00_estrutura.sql`, `04_auditoria.sql`; o admin `postgres` só no bootstrap):

| Papel | Pode | Não pode |
|---|---|---|
| `srag_etl` | dono de `raw`, `silver`, `gold`, `auditoria` | — (não é usado pelo agente) |
| `srag_agente` | `SELECT` só na `gold`; sessão `default_transaction_read_only` e `statement_timeout = 5s`, reafirmados em cada consulta (`SET TRANSACTION READ ONLY` + `SET LOCAL`) | ler `raw`, `silver`, `auditoria`; gravar em qualquer lugar |
| `srag_auditor` | `SELECT` e `INSERT` **por coluna** na `auditoria`; `UPDATE` só nas colunas de encerramento de `execucoes` | ler dados; `DELETE`; mexer em `gold`/`silver`/`raw` |

As views da gold rodam com o privilégio do dono: o agente lê o agregado sem ler a silver.

**Trilha** (`auditoria/registro.py`):

- `auditoria.execucoes`: provedor, modelo, parâmetros, versão do código (`git` sha, com
  `-modificado` se houver alteração), hash dos prompts, data de referência, contagem da silver,
  tokens e custo estimado.
- `auditoria.eventos`: nó, tipo (`llm`, `ferramenta`, `guardrail`, `decisao`), ferramenta,
  argumentos, **motivo**, resumo do resultado (limitado a 2000 caracteres por campo), duração,
  tokens, erro. O evento `guardrail_saida` guarda as notícias citadas pelo texto conferido
  (ADR 0009); o `erro` de uma saída mal formada do redator guarda só nomes de tipo, nunca a
  mensagem (com function_calling ela traz o texto cru do modelo).
- O banco repete as regras por conta própria: a execução nasce `em_andamento` (o `INSERT` não
  inclui status, fim nem consumo); um `CHECK` amarra status e fim; um gatilho só deixa encerrar
  uma vez, com `fim` = relógio do servidor; outro recusa evento em execução já encerrada.
- O mesmo conteúdo vai para `relatorios/<pasta>/auditoria.jsonl` (gravação atômica: arquivo
  temporário + troca) e para o anexo "Como este relatório foi gerado".
- Dado pessoal é mascarado em argumentos, resultado, motivo e erro antes de gravar. A trilha
  guarda só agregados, nunca linhas da base.

## Alternativas consideradas

- **Um único usuário de aplicação:** descartada; o agente leria a silver e gravaria na auditoria.
- **Auditoria só em arquivo:** descartada; sem consulta SQL e sem regra imposta pelo banco.
- **Auditoria só no banco:** descartada; o avaliador quer ver a trilha ao lado do relatório sem
  subir nada.
- **Langfuse como trilha principal:** descartado; serviço externo. Fica opcional (`nucleo.py`):
  liga só com as duas chaves no `.env` e o extra `observabilidade` instalado, e o que sai passa
  pelo mesmo mascaramento da auditoria.

## Consequências

- Uma execução pode ser auditada por SQL (ex.: `SELECT tipo, ferramenta, motivo FROM
  auditoria.eventos WHERE id_execucao = ... ORDER BY sequencia`) ou pelo JSONL.
- Toda carga reaplica as permissões do zero (`REVOKE` antes do `GRANT`).

## Limites conhecidos

- O auditor ainda pode encerrar **qualquer** execução aberta e gravar consumo inventado no
  encerramento. A garantia real é mais estreita: nenhuma execução nasce encerrada num passo só,
  o horário de fim é do servidor e o encerrado não muda mais.
- O JSONL não leva `fsync`; uma queda de energia no instante da gravação pode perder o arquivo
  (nunca deixa um pela metade).
- Se o próprio auditor cair no meio de uma execução (banco fora do ar), a execução fica
  `em_andamento`: o código não finge que encerrou, e o erro original sobe com as falhas da
  auditoria anexadas. Para listar as órfãs (a hora vem do servidor), use
  `SELECT id_execucao, inicio FROM auditoria.execucoes WHERE status = 'em_andamento' AND inicio < now() - interval '1 hour';`
- O `motivo` é declarado pelo LLM (ADR 0001).
- Tokens de resposta mal formada (redator ou classificador): entram no total e no custo os que
  o provedor informa no `usage` da resposta crua (`include_raw=True` a devolve mesmo quando a
  leitura falha). Resposta crua sem `usage` conta 0, e chamada que falha sem resposta (timeout,
  erro HTTP) não tem uso a contar: nesses casos o custo registrado fica abaixo do real.
- A carga (`04_auditoria.sql`) trava `eventos` por alguns instantes; o tempo de espera do
  auditor durante a carga não foi medido.

## Limites declarados (auditoria de segurança, 18/10/2026)

1. **TEMPORARY revogado de PUBLIC** (`00_estrutura.sql`, nome do banco por `current_database()`):
   sem isso o agente criava tabela temporária fora dos GRANTs. Nenhum papel precisa dele (a carga
   usa `COPY` direto; o auditor grava por coluna). Teste: `test_permissoes.py`.
2. **CONNECT continua com PUBLIC, de propósito.** Revogá-lo arrisca quebrar o bootstrap (o admin
   e os papéis entram em `postgres` e no banco do projeto antes de qualquer GRANT) e não protege
   dado: sem USAGE em `raw`/`silver`/`auditoria` quem conecta não lê nada, e a porta do Postgres
   é local. A defesa é o USAGE por esquema, não a porta do banco.
3. **Supressão de células `<5` vale para a saída do agente (ADR 0011), não para o papel do
   banco.** A `gold` guarda contagens pequenas; o microdado de origem já é público no OpenDATASUS.
   Quem tem `srag_agente` e SQL livre vê essas contagens.
4. **O mascaramento de PII cobre documento, telefone e e-mail; não cobre nome nem município.** A
   pergunta do usuário vai ao classificador (OpenRouter) e fica nos parâmetros da auditoria (na demonstração online o campo de pergunta complementar fica
   desligado e o texto livre nunca é gravado). O
   `termo` de busca de notícias, que vai a Tavily e Google, é recusado se tiver PII ou instrução.
5. **GitHub Actions e imagens Docker são fixadas por tag, não por SHA.** Uma tag republicada
   chegaria ao CI sem aviso; fixar por digest fica como melhoria futura.

## Como conferir

`tests/integracao/test_permissoes.py`, `tests/integracao/test_auditoria_postgres.py`,
`tests/unit/test_auditoria.py`, `tests/unit/test_auditoria_pii.py`; `make testes`.
