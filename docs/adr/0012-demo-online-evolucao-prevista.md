# ADR 0012 — Demonstração online fica como evolução prevista

- **Status:** **substituída pelo [ADR 0013](0013-demonstracao-online.md)** (18/10/2026). Antes:
  aceita como não implementar agora, com o desenho abaixo como proposta (spec §10, "deploy em
  nuvem"). O texto abaixo fica como registro; o que vale é o ADR 0013

## Contexto

O escopo do projeto é local e reprodutível por decisão: quem avalia clona o repositório, sobe o
Postgres no Docker e roda `make tudo` com a própria chave de LLM. Deploy em nuvem ficou fora do
escopo no desenho (spec §10). Várias garantias atuais dependem disso:

- o banco é publicado só em `127.0.0.1:5435` (`docker-compose.yml`);
- a tela se recusa a rodar fora de `127.0.0.1` (`app.py`, `.streamlit/config.toml`);
- o gasto de LLM é de quem roda, limitado por execução (ADR 0005) e, na tela, por uma trava global
  e um teto por sessão, que é contornável recarregando a página (risco aceito no README porque a
  tela é local).

Uma demonstração online ajudaria quem quer ver o agente sem instalar nada, mas abre o custo de LLM
a terceiros e tira o projeto do ambiente em que as proteções acima foram pensadas.

## Decisão

**Não implementar a demonstração online nesta entrega.** Nada do desenho abaixo existe no código:
não há `DATABASE_URL` em `config.py`, não há teto global diário e a tela continua presa a
`127.0.0.1`. Fica registrado o desenho, os riscos e o que precisa estar pronto antes de fazer.

## Desenho previsto

| Peça | Escolha prevista | Por quê |
|---|---|---|
| Aplicação | Streamlit Community Cloud, publicando a mesma `app.py` | a tela já existe e chama o mesmo núcleo da CLI (mesmas guardrails e auditoria) |
| Banco | Postgres gerenciado gratuito com pgvector (Neon) | a RAG e a auditoria já usam Postgres + pgvector; nenhuma troca de tecnologia |
| Dados na nuvem | **só os agregados da `gold`, materializados** em tabelas, mais `dicionario_trechos` e o schema `auditoria` | o agente só lê a gold (ADR 0010); raw e silver nunca saem da máquina local |
| Conexão | URL de conexão no formato `DATABASE_URL`, uma por papel, lida pelo `Config` | o formato que provedores gerenciados entregam; hoje o projeto monta a conexão com `PG_HOST`, `PG_PORTA` e senhas por papel |
| Papéis | `srag_agente` (só leitura da gold) e `srag_auditor` (só auditoria) recriados no banco gerenciado; o papel de carga não vai para a nuvem | privilégio mínimo igual ao local |
| Gasto | teto global diário de relatórios, contado em `auditoria.execucoes` e conferido antes de gerar; limite de gasto configurado na própria chave do provedor de LLM | o teto por sessão não segura um público aberto; o limite na chave é a última barreira, fora do código |
| Segredos | nos *secrets* da plataforma, nunca no repositório (o `.env` continua fora do Git) | mesma regra de hoje, com `gitleaks` no CI |
| Endereço | subdomínio próprio por redirecionamento para o endereço da plataforma | a plataforma gratuita não serve domínio próprio diretamente; o redirecionamento não exige servidor |

Para a materialização: a gold de hoje são views sobre a silver. A carga para a nuvem rodaria
localmente (`make carga`, depois uma exportação do resultado das views da gold) e só o resultado
subiria. Como a base é um retrato fechado, os agregados não mudam até a próxima versão do arquivo.

## Alternativas consideradas

- **Não ter demonstração nenhuma:** é o estado atual; o relatório de exemplo versionado em
  `relatorios/exemplo/` já mostra o resultado sem custo para ninguém.
- **Subir o Compose inteiro num servidor:** descartada; levaria raw e silver (dado de saúde
  linha a linha) para fora da máquina local e criaria um servidor para manter.
- **Demonstração só com o relatório pronto, sem LLM:** possível e barata, mas não mostra o agente
  decidindo; é o que o exemplo versionado já cumpre.

## Riscos

- **Hibernação:** o app gratuito dorme sem acesso e o banco gratuito suspende quando ocioso. A
  primeira visita depois disso demora; quem avalia pode achar que está quebrado.
- **Memória:** o limite de memória do plano gratuito do Streamlit Community Cloud **não foi
  confirmado** para este projeto. O modelo de embeddings (`fastembed`) é carregado no mesmo
  processo da tela; é preciso medir antes de publicar.
- **Custo aberto a terceiros:** qualquer visitante gera relatório com a chave do autor. Teto global
  diário e limite na chave reduzem, não eliminam; um abuso esgota a cota e derruba a demonstração
  até o dia seguinte.
- **Dado pessoal digitado por visitante:** a pergunta complementar vai ao classificador (LLM
  externo) e à auditoria depois do guardrail de entrada, que não mascara nome nem município
  (ADR 0010, limite 4). Num público aberto isso fica mais provável.
- **Contagens pequenas na nuvem:** a gold guarda contagens pequenas; a supressão vale para a saída
  do agente (ADR 0010, limite 3). Quem obtivesse a credencial do papel de leitura as veria.

## Critérios para fazer

1. Teto global diário implementado e testado (inclusive com duas sessões simultâneas).
2. Limite de gasto configurado na chave do provedor antes da primeira publicação.
3. Memória do app medida com o modelo de embeddings carregado, dentro do limite do plano.
4. Um ADR novo que substitua a regra "a tela só roda em `127.0.0.1`" e diga quais controles
   passam a valer no lugar dela.
5. Banco gerenciado com os mesmos papéis e o mesmo teste de permissões
   (`tests/integracao/test_permissoes.py`) rodando contra ele.
6. Nenhum dado de raw ou silver no banco gerenciado, conferido por consulta ao catálogo do banco.

## Como conferir

Hoje não há o que rodar: a conferência de que nada disto foi implementado é a ausência de
`DATABASE_URL` em `src/srag_agente/config.py` e a checagem de endereço em `src/srag_agente/app.py`.
