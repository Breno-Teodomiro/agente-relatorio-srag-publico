# Como subir a sua própria demonstração online

Roteiro para publicar uma demonstração do agente com contas gratuitas: o banco num Postgres
gerenciado com `pgvector` (este roteiro usa o Neon) e a tela no Streamlit Community Cloud. A decisão,
os controles e os riscos aceitos estão no [ADR 0013](adr/0013-demonstracao-online.md).

A demonstração é **opcional**. O caminho principal do projeto continua sendo local (`make tudo`, no
[README](../README.md#início-rápido)), e nada aqui muda o comportamento local.

## Sumário

- [O que vai para a nuvem e o que não vai](#o-que-vai-para-a-nuvem-e-o-que-não-vai)
- [Pré-requisitos](#pré-requisitos)
- [1. Carregar e indexar localmente](#1-carregar-e-indexar-localmente)
- [2. Exportar o pacote da demonstração](#2-exportar-o-pacote-da-demonstração)
- [3. Criar o banco no provedor](#3-criar-o-banco-no-provedor)
- [4. Preencher o `.env.demo`](#4-preencher-o-envdemo)
- [5. Carregar e conferir o banco remoto](#5-carregar-e-conferir-o-banco-remoto)
- [6. Limitar o gasto na chave do provedor de LLM](#6-limitar-o-gasto-na-chave-do-provedor-de-llm)
- [7. Criar o app na plataforma](#7-criar-o-app-na-plataforma)
- [8. Conferir depois de publicar](#8-conferir-depois-de-publicar)
- [Tetos diários: conferir e ajustar](#tetos-diários-conferir-e-ajustar)
- [Atualizar os dados](#atualizar-os-dados)
- [Derrubar a demonstração](#derrubar-a-demonstração)
- [Solução de problemas](#solução-de-problemas)

---

## O que vai para a nuvem e o que não vai

| Vai | Não vai |
|---|---|
| o resultado das views da gold (agregados por UF, dia, classificação e faixa etária), como tabelas | `raw` e `silver`: nenhuma linha de caso sai da sua máquina |
| `gold.dicionario_trechos` (texto do dicionário público e vetores) | o zip da base, o `.env` local, as senhas do dono do banco e do papel de carga |
| o esquema `auditoria` (trilha das execuções da demonstração) | a pergunta complementar: fica desligada na demonstração |
| os papéis `srag_agente` e `srag_auditor`, criados pela carga | o papel de carga `srag_etl` |

Na demonstração, a busca do dicionário roda sem o modelo de embeddings (`BUSCA_DICIONARIO=texto`:
nome exato do campo e busca de texto completo do Postgres), para caber na memória do plano gratuito.

## Pré-requisitos

| Para | Precisa de |
|---|---|
| gerar o pacote | o projeto rodando localmente: `uv`, Docker com Compose, `make` e o zip da base (passos 1 a 3 do [Início rápido](../README.md#início-rápido)) |
| o banco | conta gratuita num Postgres gerenciado com `pgvector` e TLS (o Neon atende; o plano gratuito é de 1 GB por projeto, segundo a documentação do provedor) |
| a tela | conta gratuita no [Streamlit Community Cloud](https://streamlit.io/cloud), ligada à sua conta do GitHub, e um fork deste repositório |
| o relatório | uma chave do OpenRouter **dedicada à demonstração**, com limite de gasto (passo 6) |
| notícias pela Tavily | chave da Tavily (opcional: sem ela, o agente usa o Google News RSS) |

## 1. Carregar e indexar localmente

```bash
make subir
make carga
make indexar
```

O pacote sai da gold local, então ela precisa estar carregada e com o dicionário indexado.

## 2. Exportar o pacote da demonstração

```bash
make demo-exportar        # o mesmo que: uv run srag demo exportar
```

O que acontece:

- a exportação lê a gold **com o papel do agente**, que não tem acesso a `raw`, `silver` nem
  `auditoria`; a ausência de dado de caso depende de permissão do banco, não de cuidado de quem roda;
- grava em `demo/pacote/` um CSV por tabela, só com as colunas da lista fechada em
  [`src/srag_agente/demo/__init__.py`](../src/srag_agente/demo/__init__.py), e um `manifesto.json`
  com o sha256 de cada arquivo;
- falha se aparecer coluna nova na gold ("revise antes de publicar"), coluna de nome proibido ou
  valor fora do domínio esperado;
- termina com o sha256 do manifesto e, se houver, avisos sobre o texto do dicionário (o PDF é
  público; o aviso é para você ler, não bloqueia).

Conferência à mão, antes de carregar:

```bash
head -1 demo/pacote/*.csv     # só os cabeçalhos da lista
du -sh demo/pacote            # tamanho do pacote
```

A pasta `demo/pacote/` é ignorada pelo Git: o pacote é derivado e se refaz com `make carga indexar
demo-exportar`.

## 3. Criar o banco no provedor

No painel do provedor (no Neon: criar um projeto, Postgres 16 ou mais novo):

1. Anote o **host direto**, o nome do banco, o papel **dono** e a senha do dono. No Neon, a janela de
   conexão mostra o host com e sem o *pooler*: use o que **não** tem `-pooler` no nome.
2. **Não crie** `srag_agente` nem `srag_auditor` pelo painel. Papel criado pelo painel do Neon herda
   privilégios de administrador; a carga cria os dois por SQL, sem esses privilégios.

## 4. Preencher o `.env.demo`

```bash
cp .env.demo.example .env.demo
```

O [`.env.demo.example`](../.env.demo.example) explica cada campo. Em resumo:

| Campo | O que pôr |
|---|---|
| `PG_HOST`, `PG_PORTA`, `PG_BANCO` | host direto, porta e banco do passo 3 |
| `PG_USUARIO_ADMIN`, `PG_SENHA_ADMIN` | o dono do banco e a senha dele (só a carga usa; a tela nunca recebe) |
| `PG_SSLMODE`, `PG_SSLROOTCERT` | `verify-full` e `/etc/ssl/certs/ca-certificates.crt` (verifica o certificado do servidor; é o caminho do Debian/Ubuntu, use o do seu SO se for outro). O valor `system` não funcionou com a libpq do driver. Se a conexão falhar por certificado, `require` |
| `PG_CONNECT_TIMEOUT` | segundos de espera da conexão (o banco gratuito suspende quando ocioso e demora a acordar) |
| `PG_SENHA_AGENTE`, `PG_SENHA_AUDITOR` | duas senhas novas, uma por papel, geradas como abaixo |

Para gerar cada senha (bem acima do mínimo de entropia que o Neon exige):

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

O `.env.demo` é ignorado pelo Git. Não cole o conteúdo dele em chat, e-mail, issue ou print.

Teste a conexão do dono com TLS antes de carregar (não imprime senha):

```bash
uv run python -c "from pathlib import Path; import psycopg; from srag_agente.demo.carregar import ler_config_demo; c = ler_config_demo(Path('.env.demo')); psycopg.connect(c.dsn('admin')).close(); print('ok', c.pg_sslmode)"
```

## 5. Carregar e conferir o banco remoto

```bash
make demo-carregar ALVO=seu-host-direto.exemplo.com
make demo-conferir ALVO=seu-host-direto.exemplo.com
```

O `ALVO` é o mesmo `PG_HOST` do `.env.demo`, digitado: é a confirmação de qual banco vai receber a
carga. Os alvos do `make` chamam `uv run srag demo carregar --env-arquivo .env.demo --alvo <host>
--senha-em-texto` e `uv run srag demo conferir --env-arquivo .env.demo --alvo <host>`.

**A carga recusa, sem escrever nada:**

- `ALVO` diferente do `PG_HOST` do arquivo;
- host local (`localhost`, `127.0.0.1`, o banco do Compose) ou `PG_SSLMODE` fraco;
- banco que tenha esquema `raw` ou `silver` (parece o banco local);
- tabela da gold que já exista com colunas diferentes da lista (confira antes; a carga nunca apaga
  tabela);
- pacote adulterado (sha256 diferente do manifesto) ou com coluna fora da lista.

**O que ela faz, numa transação só** (falhou, nada muda): cria a extensão `vector` (o dono do banco gerenciado pode instalá-la; provado numa carga real), cria os papéis
`srag_agente` e `srag_auditor` se faltarem, cria as tabelas da gold e da auditoria, troca o conteúdo
da gold pelo do pacote, aplica o teto diário e registra o pacote em `gold.pacote_demo`. Antes do
`COMMIT` confere linhas por tabela, que não há objeto fora de `gold` e `auditoria` e que nenhum dos
dois papéis tem poder de administrador. Recarregar não reenvia senha nem apaga a trilha.

`--senha-em-texto` é o padrão (a saída é `--senha-em-hash`, só para provedor que aceite hash) porque o
Neon não aceita senha em hash: ela vai em texto, protegida pelo TLS verificado, só quando o papel
ainda não existe. A carga **recusa** enviar senha em texto com `PG_SSLMODE` diferente de
`verify-full`, a não ser com `--aceitar-tls-sem-verificacao` (por sua conta e risco: quem se pusesse
no meio leria a senha). Para trocar as senhas dos papéis depois:

```bash
uv run srag demo carregar --env-arquivo .env.demo --alvo seu-host-direto.exemplo.com \
  --senha-em-texto --trocar-senhas
```

**A conferência** entra com as credenciais da tela (agente e auditor) e tenta o que cada papel não
pode fazer: gravar na gold, ler a auditoria como agente, criar tabela temporária, apagar ou alterar
a trilha como auditor, ler a gold como auditor. Também confere o catálogo e o gatilho do teto. Não
escreve nada. Cada linha sai `[ok]` ou `[FALHOU]`, e o comando termina com código 0 só se todas
passarem.

Opcional, no editor SQL do provedor, depois da primeira carga (a extensão `pg_stat_statements` não
estava instalada no banco da carga real; se também não estiver no seu, o comando dá erro e não há o
que conferir). O texto do `CREATE ROLE` leva a senha, e quem rodar o comando de carga fora da CLI vê
host e usuário no erro bruto do driver:

```sql
SELECT query FROM pg_stat_statements WHERE query ILIKE '%password%';
```

Se aparecer uma senha, troque-a com `--trocar-senhas`.

## 6. Limitar o gasto na chave do provedor de LLM

O teto diário da tela (próxima seção) conta o gasto **estimado**. A última barreira fica fora do
código:

1. Crie no OpenRouter uma chave **só para a demonstração**, separada da que você usa localmente.
2. Defina um limite de crédito para ela, no valor que você aceita perder num dia ruim. Se o provedor
   não oferecer limite por chave, mantenha na conta só esse crédito.
3. **A demonstração só funciona com a reserva do provedor de LLM desligada.** Não ponha
   `OPENAI_API_KEY` nos *secrets*: a reserva automática escaparia do limite da chave dedicada. A tela
   recusa abrir se ela estiver lá, e também se o provedor não for o OpenRouter com a chave definida.
4. Se usar a Tavily, confira no painel dela a cota do plano.
5. O gasto por relatório é uma **estimativa**, e a barreira final é o limite de crédito da chave: o
   redator pode fazer até 3 tentativas sem que os tokens delas sejam conferidos antes.

## 7. Criar o app na plataforma

No Streamlit Community Cloud, crie um app a partir do seu fork:

| Campo | Valor |
|---|---|
| Repositório e branch | o seu fork, branch `main` |
| *Main file path* | `demo/streamlit_app.py` |
| *Advanced settings*, versão do Python | 3.12 |
| *Advanced settings*, *Secrets* | o bloco abaixo |

A entrada `demo/streamlit_app.py` põe `src/` no caminho de importação e chama a mesma tela do
`make app`. As dependências vêm de `demo/requirements.txt`, gerado do `uv.lock` por
`make demo-requisitos` (o CI confere que está em dia): segundo a documentação oficial da plataforma,
as dependências são lidas primeiro da pasta do arquivo principal, por isso o arquivo fica em `demo/`.

O que a plataforma publica, **segundo a documentação oficial** (não medido aqui):

- repositório público gera app público, e o subdomínio `*.streamlit.app` é editável (6 a 63
  caracteres);
- o limite de memória publicado por app vai de 690 MB (mínimo) a 2,7 GB (máximo) e pode mudar sem
  aviso;
- o app hiberna depois de 12 h sem tráfego;
- o disco local não é garantido persistente (os relatórios ficam na pasta temporária e são
  descartáveis);
- *secrets* de nível raiz também viram variáveis de ambiente; tabelas aninhadas não.

**Bloco de *secrets*** (TOML; todo valor entre aspas duplas). Os valores abaixo são exemplos falsos:
troque pelos seus. Use **exatamente** estes campos:

```toml
DEMO_ONLINE = "1"
BUSCA_DICIONARIO = "texto"
PG_HOST = "seu-host-direto.exemplo.com"
PG_PORTA = "5432"
PG_BANCO = "nome-do-seu-banco"
PG_SSLMODE = "verify-full"
PG_SSLROOTCERT = "/etc/ssl/certs/ca-certificates.crt"
PG_CONNECT_TIMEOUT = "15"
PG_SENHA_AGENTE = "troque-pela-senha-do-agente"
PG_SENHA_AUDITOR = "troque-pela-senha-do-auditor"
OPENROUTER_API_KEY = "troque-pela-chave-dedicada-a-demo"
TAVILY_API_KEY = "troque-pela-chave-da-tavily"
```

- `TAVILY_API_KEY` é opcional: sem a linha, as notícias vêm do Google News RSS.
- `PG_SSLMODE` e `PG_SSLROOTCERT` iguais aos do `.env.demo` que funcionou na carga.
- **Nunca** ponha `PG_SENHA_ADMIN`, `PG_SENHA_ETL`, `OPENAI_API_KEY` nem chaves do Langfuse: a tela
  confere e não abre se alguma estiver presente.
- A tela só lê do bloco as chaves de nível raiz que são campos da configuração do projeto
  ([`config.py`](../src/srag_agente/config.py)); tabela aninhada e o resto são ignorados.
- Na partida, a tela exige estas opções do Streamlit (`st.get_option`): XSRF ligado, CORS ligado,
  `client.toolbarMode` igual a `viewer` e `server.enableStaticServing` desligado. O
  `client.showErrorDetails` tem de ser exatamente `"none"`: a própria tela força esse valor antes de
  conferir, porque o valor legado `False` do Cloud equivale a `stacktrace` e entregaria o traceback
  ao navegador. `runOnSave`, `gatherUsageStats` e `fastReruns` não são conferidos (a plataforma os
  força).

Na partida, a tela confere o ambiente e o banco. Se algo faltar, o visitante vê só "A demonstração
está indisponível no momento", e o motivo vai para o log do app (visível para quem administra o app
na plataforma), sem senha nem chave.

## 8. Conferir depois de publicar

Pelo efeito, nesta ordem:

1. Abra a URL do app. O aviso do topo mostra a data de referência dos dados e "hoje: 0 de N".
2. Aba **Dicionário de dados**: pergunte "o que é VACINA_COV?". O primeiro trecho vem com a origem
   "nome exato".
3. Aba **Relatório**: gere o do Brasil. Aparecem a situação e o custo estimado. No editor SQL do
   provedor:
   ```sql
   SELECT status, custo_usd, inicio FROM auditoria.execucoes ORDER BY inicio DESC LIMIT 3;
   ```
4. Rodapé: sha256 do pacote igual ao que a exportação imprimiu.
5. Log do app: linhas `memória (partida)` e `memória (depois do relatório)`, com o uso do processo e
   o limite do contêiner.
6. `make demo-conferir ALVO=seu-host-direto.exemplo.com` continua todo `[ok]`.

## Tetos diários: conferir e ajustar

Os limites ficam na tabela `auditoria.limites_demo`, semeada por
[`src/srag_agente/demo/sql/02_teto_demo.sql`](../src/srag_agente/demo/sql/02_teto_demo.sql). Valores
iniciais: 10 relatórios, 40 buscas de notícia e US$ 1,00 de gasto estimado por dia, de meia-noite a
meia-noite no horário de Brasília. São política, não medida.

| Teto | Quem segura |
|---|---|
| relatórios por dia (todos os visitantes, qualquer situação, inclusive erro) | a tela, antes de gerar, **e** um gatilho no banco, que recusa a execução além do limite mesmo com dois processos |
| buscas de notícia por dia | a tela, antes de gerar |
| gasto estimado por dia | a tela, antes de gerar, reservando o custo máximo de cada execução aberta e da próxima |
| relatórios por sessão do navegador (`MAX_RELATORIOS_SESSAO`) | a tela; é conforto de uso, contornável recarregando a página |
| gasto real | o limite na chave do provedor (passo 6) |

Atingido o teto, o botão "Gerar relatório" fica desabilitado com o aviso; a aba Dicionário continua
funcionando.

**Ver os limites e o uso de hoje** (editor SQL do provedor, como dono):

```sql
SELECT * FROM auditoria.limites_demo;

SELECT count(*) AS relatorios,
       count(*) FILTER (WHERE status = 'em_andamento') AS abertas,
       sum(custo_usd) AS custo_estimado_usd
FROM auditoria.execucoes
WHERE inicio >= (date_trunc('day', now() AT TIME ZONE 'America/Sao_Paulo') AT TIME ZONE 'America/Sao_Paulo');

-- série dos últimos 30 dias
SELECT (inicio AT TIME ZONE 'America/Sao_Paulo')::date AS dia, count(*), sum(custo_usd)
FROM auditoria.execucoes
WHERE inicio > now() - interval '30 days'
GROUP BY 1 ORDER BY 1;
```

Compare o `custo_usd` estimado com o valor faturado no painel do provedor de LLM de tempos em tempos:
a estimativa usa o preço configurado e não conta chamada que falhou sem resposta.

**Mudar um limite:** edite a semente no `02_teto_demo.sql`, faça commit (o histórico do arquivo é a
auditoria da regra) e rode `make demo-carregar ALVO=...` de novo. A recarga não reenvia senha nem
apaga a trilha.

**Testar o bloqueio:** baixe `relatorios_dia` para o número já usado hoje, carregue, recarregue a
tela (botão desabilitado, mensagem do teto, Dicionário funcionando), volte o valor e carregue de
novo.

## Atualizar os dados

Nova versão da base: `make carga indexar demo-exportar` e `make demo-carregar ALVO=...`. A troca do
conteúdo da gold é atômica (quem está na tela vê os dados antigos até o fim da carga), e cada carga
ganha uma linha em `gold.pacote_demo`. Antes de publicar outra versão da base, rode a conferência de
blocos de um dia descrita no [ADR 0011](adr/0011-supressao-de-celulas-pequenas.md).

## Derrubar a demonstração

**Parar a geração de relatórios, mantendo o app:** revogue a chave dedicada no painel do OpenRouter.
Os relatórios passam a falhar sem custo; o dicionário continua. Para tirar a tela do ar sem apagar
nada, remova `OPENROUTER_API_KEY` dos *secrets*: a tela passa a mostrar "indisponível".

**Derrubar de vez:**

1. Exclua o app no painel do Streamlit Community Cloud.
2. Revogue as chaves do OpenRouter e da Tavily usadas na demonstração.
3. No provedor do banco, exclua o projeto (apaga a gold e a trilha). Para guardar a trilha antes,
   exporte `auditoria.execucoes` e `auditoria.eventos` pelo editor SQL; para só cortar o acesso,
   troque as senhas com `--trocar-senhas` sem atualizar os *secrets*.
4. Na sua máquina, apague o `.env.demo` e a pasta `demo/pacote/`.

**Liberar espaço da trilha**, se o banco gratuito chegar perto do limite (rodar como dono, nesta
ordem; o auditor não tem `DELETE` de propósito):

```sql
SELECT pg_size_pretty(pg_total_relation_size('auditoria.eventos')
                    + pg_total_relation_size('auditoria.execucoes'));

DELETE FROM auditoria.eventos WHERE id_execucao IN
  (SELECT id_execucao FROM auditoria.execucoes WHERE inicio < now() - interval '30 days');
DELETE FROM auditoria.execucoes WHERE inicio < now() - interval '30 days';
```

## Solução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| a carga diz que o alvo é diferente | `ALVO` não é igual ao `PG_HOST` do `.env.demo` | copie o host do arquivo; maiúsculas não importam |
| a carga recusa o host | host local ou `PG_SSLMODE` fora de `require`/`verify-ca`/`verify-full` | aponte para o host do provedor e use TLS |
| a carga diz que o banco tem `raw`/`silver` | o `.env.demo` aponta para um banco com as camadas locais | confira o host e o banco; a demonstração usa um banco só dela |
| a carga recusa por esquema divergente | a tabela já existe no banco remoto com outras colunas | compare as colunas no editor SQL; se o banco for só da demonstração, apague a tabela à mão e carregue de novo |
| falha de certificado na conexão (`certificate verify failed`) | `PG_SSLROOTCERT=system` (não funciona com a libpq do driver) ou caminho do arquivo de certificados inexistente no sistema | use `PG_SSLROOTCERT=/etc/ssl/certs/ca-certificates.crt` (ou o caminho do seu SO); só em último caso `PG_SSLMODE=require`, sem verificar o servidor, e a carga exigirá `--aceitar-tls-sem-verificacao` para enviar senha |
| erro ao criar a extensão `vector` ou ao aplicar permissão | o dono do banco gerenciado não pode rodar aquele comando | rode o comando recusado no editor SQL do provedor como dono e carregue de novo |
| `demo-conferir` com linha `[FALHOU]` | papel com privilégio a mais (por exemplo, criado pelo painel) ou carga incompleta | não publique; corrija e rode de novo. Papel criado pelo painel: apague-o e deixe a carga criar |
| "A demonstração está indisponível no momento" com a linha de log citando `client.toolbarMode`, `server.enableXsrfProtection`, `server.enableCORS` ou `server.enableStaticServing` | a plataforma ou o `.streamlit/config.toml` do fork mudou a opção | volte a opção ao valor exigido (XSRF e CORS ligados, `toolbarMode = "viewer"`, arquivos estáticos desligados) |
| build do app falha | arquivo de dependências diferente do esperado | leia o log de build; confira que `demo/requirements.txt` está em dia (`make demo-requisitos`) |
| a tela diz que só roda em `127.0.0.1` | `DEMO_ONLINE` ausente ou falso nos *secrets* | confira `DEMO_ONLINE = "1"` |
| o app não responde e o log mostra o servidor só em `127.0.0.1` | a plataforma respeitou o `address` do `.streamlit/config.toml` | tire a linha `address` do `config.toml` no seu fork (o `make app` local já passa o endereço na linha de comando) |
| "A demonstração está indisponível no momento" | alguma conferência da partida falhou | leia a linha `demonstração recusada na partida` no log do app: ela diz qual variável ou opção, sem o valor |
| primeira visita demora | o app (hiberna após 12 h sem tráfego, segundo a documentação da plataforma) ou o banco gratuito estavam hibernando | espere; se passar do `PG_CONNECT_TIMEOUT`, aumente o valor |
| botão "Gerar relatório" desabilitado | teto do dia ou da sessão atingido, ou o banco não respondeu à conferência do uso | veja a seção dos tetos; renova à meia-noite (horário de Brasília) |
| o app reinicia sozinho | falta de memória | confira as linhas de memória no log; mantenha `BUSCA_DICIONARIO = "texto"` |
| versão do código `desconhecida` na trilha | sem `git` no contêiner da plataforma | limite conhecido do ADR 0013 |
| os números não batem com o seu banco local | o banco remoto tem o pacote de outra carga | compare o sha256 do rodapé com o da última exportação; exporte e carregue de novo |
