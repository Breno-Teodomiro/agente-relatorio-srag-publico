# ADR 0001 — Orquestrador com ferramentas fechadas

- **Status:** aceita (14/10/2026, spec §3)

## Contexto

O relatório precisa de números exatos de uma base de saúde e de comentários embasados em notícias.
A avaliação pede arquitetura clara, auditoria das decisões do agente, guardrails e cuidado com dado
sensível. Um agente que escreve SQL enxergaria o schema inteiro e poderia errar ou inventar cálculo.

## Decisão

- Um único orquestrador LangGraph (`agente/grafo.py`): `guardrail_entrada → orquestrador ⇄
  ferramentas → verificar_cobertura → redator → guardrail_saida`, com arestas condicionais
  explícitas. O grafo real é gerado do código (`estrutura_mermaid()`).
- Cinco ferramentas fechadas (`ferramentas/registro.py`): `consultar_metricas`, `consultar_serie`,
  `consultar_distribuicao`, `buscar_noticias`, `consultar_dicionario`. Parâmetros em Pydantic com
  `extra="forbid"` e valores fechados (`Literal` para métrica, série, dimensão, janela e UF).
  **Nenhuma aceita SQL**: o SQL é fixo, com bind, em `metricas/repositorio.py`.
- Campo **`motivo` obrigatório** em toda chamada (10 a 300 caracteres, ao menos uma letra). Vai
  para a auditoria (ADR 0010).
- `verificar_cobertura` é código, não LLM: exige as 4 métricas, as 2 séries e as 2 janelas de
  notícias; faltando algo, devolve ao orquestrador (máx. 2 voltas).
- Argumento recusado volta ao LLM como `ArgumentosInvalidos`, com texto limpo (sem o valor
  recusado nem o nome do campo inventado), para não levar dado pessoal da pergunta ao log.

## Alternativas consideradas

- **Pipeline fixo, LLM só redige:** descartada; não há agente nem decisão de ferramenta.
- **Text-to-SQL livre:** descartada; o LLM poderia ler colunas sensíveis e errar a conta.
- **Multiagente:** descartada para a PoC; mais chamadas, mais custo e mais difícil de auditar.
  Fica como ponto de encaixe (subgrafos chamando as mesmas ferramentas).

## Consequências

- Métrica nova = view na gold + entrada no catálogo + ramo em `_partes` (`metricas/catalogo.py`).
- Todo acesso ao banco é previsível, testável sem LLM (dublês em `tests/fakes.py`) e auditável.
- O LLM decide *o que* consultar e *escreve*; não calcula nada (ADR 0003).

## Limites conhecidos

- O LLM pode escolher mal a ordem das chamadas ou gastar o teto (12 chamadas, ADR 0005); a
  cobertura por código garante o mínimo, não a melhor sequência.
- O `motivo` é declarado pelo próprio LLM: registra a intenção alegada, não prova a intenção real.

## Como conferir

`tests/agente/test_grafo.py` (rotas, cobertura, chamada inválida) e `tests/unit/` das ferramentas;
rodar `make testes`.
