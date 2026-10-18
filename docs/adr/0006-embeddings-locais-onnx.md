# ADR 0006 — Embeddings locais (fastembed/ONNX) no pgvector, com busca híbrida por nome de campo

- **Status:** aceita (14/10/2026, spec §8; busca híbrida acrescentada na Task 8)

## Contexto

O agente precisa explicar campos da base (o que é `EVOLUCAO = 3`, o que conta como UTI) a partir
do dicionário de dados oficial (PDF público do DATASUS, versionado em `dados/referencia/`). O
avaliador precisa conseguir rodar tudo sem chave extra e sem custo.

## Decisão

- **Modelo local via `fastembed` (ONNX Runtime, CPU):**
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensões
  (`rag/embeddings.py`). Baixado uma vez para `.cache/modelos`.
- **Armazenamento no próprio Postgres do projeto** (extensão pgvector, sem sistema novo):
  `gold.dicionario_trechos(campo, pagina, texto, embedding vector(384))`, lida pelo papel do
  agente; distância de cosseno (`<=>`).
- **Indexação como ETL** (`rag/indexar.py`, `make indexar`): o PDF é dividido por campo, usando os
  nomes de coluna da `raw` para reconhecer onde começa cada campo. Falha **antes** de apagar o
  índice existente se faltar a raw ou algum dos campos das métricas; a troca é numa transação só.
- **Busca híbrida** (`ferramentas/dicionario.py`): nomes de campo citados em maiúsculas
  (`UTI`, `VACINA_COV`) vão primeiro por igualdade exata; o resto completa por similaridade;
  `k = 3`. O trecho chega ao LLM embrulhado como dado externo, com a página de origem.

## Alternativas consideradas

- **API de embeddings (OpenAI e afins):** descartada; exige chave e custo, e a reprodução pelo
  avaliador passaria a depender de conta externa.
- **Colar o dicionário inteiro no prompt:** descartada; gasta tokens a cada execução e dilui o
  contexto.
- **Banco vetorial separado:** descartado; o pgvector já está no Postgres do projeto, e outro
  sistema traria backup e operação a mais sem ganho medido.
- **Reranqueador:** adiado; a ferramenta usa `k = 3` e as métricas citam campos pelo nome.

## Consequências

- Zero custo por consulta e nenhuma chave nova; a primeira execução baixa o modelo.
- Sem índice ANN (HNSW/IVFFlat): a busca é varredura exata. Com o volume de um dicionário de
  dados isso basta; se a tabela crescer, medir com `EXPLAIN ANALYZE` a consulta `_SQL_BUSCA`
  antes de criar índice.

## Limites conhecidos

- **Qualidade medida, não escrita aqui:** `make avaliar-rag` roda as perguntas de
  `tests/dados/perguntas_rag.json` e imprime o acerto no 1º lugar e entre os 3 primeiros. Na
  medição feita na Task 8, a busca foi boa com o campo citado pelo nome e fraca em linguagem
  natural livre. Rodar logo depois de `make indexar` no banco real, nunca depois do `pytest`
  apontando para o mesmo banco (os testes reindexam `srag_teste` com texto falso).
- O arquivo do modelo é baixado pelo `fastembed` na primeira execução, sem revisão nem hash
  fixados no código: a versão baixada pode mudar entre máquinas. O pacote `fastembed` está travado no `uv.lock`; o arquivo do modelo não.
- O divisor depende do layout do PDF desta versão do dicionário; outra versão pede reconferência.
