# ADR 0006 — Embeddings locais (fastembed/ONNX) no pgvector, com busca híbrida por nome de campo

- **Status:** aceita (14/10/2026, spec §8; busca híbrida acrescentada na Task 8; divisão por
  linha da tabela e conjunto de validação acrescentados em 18/10/2026)

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
- **Indexação como ETL** (`rag/indexar.py`, `make indexar`): o dicionário é uma tabela (nome,
  tipo, categoria, descrição, características, DBF). A detecção de tabela do PyMuPDF dá uma linha
  por campo; a linha cortada pela quebra de página (sem DBF) é colada à anterior; a coluna DBF é
  casada com os nomes de coluna da `raw`. Campo do dicionário que a base pública não traz (CPF,
  CNS, nome, endereço) não vira trecho. O texto começa por `Campo <NOME>: <rótulo>`, porque o
  modelo trunca a entrada (128 tokens, pela documentação do fastembed). Falha **antes** de apagar
  o índice existente se faltar a raw ou algum dos campos das métricas; a troca é numa transação só.
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
- **Divisão por texto corrido** (a versão anterior: o nome do campo fechava o trecho): substituída.
  Só reconhecia nomes da raw, então o campo ausente da base (CPF, CNS, nome) fazia o vizinho
  engolir os anteriores (o trecho do sexo começava no CNS), e a categoria que caía na página
  seguinte ia para o campo seguinte ("2-Feminino" no trecho da data de nascimento).
- **Experimentos medidos e descartados** (critério na seção de qualidade):
  - *Vetor só do rótulo + descrição* (sem tipo, categorias e características), guardando o texto
    completo para o LLM: na validação, ganhou num indicador e perdeu no outro; ficou o mais
    simples, vetor do mesmo texto que o LLM recebe.
  - *Fusão lexical* (BM25 sobre o trecho, fundida à similaridade por RRF): não melhorou a
    validação com o peso fixado antes da medição; o peso que parecia melhor foi escolhido olhando
    o resultado, e por isso não conta.
  - *Modelo maior* (`paraphrase-multilingual-mpnet-base-v2`, 768 dimensões): melhorou o 1º lugar e
    não o top-3, que é o que a ferramenta entrega (`k = 3`); exige trocar a coluna `vector(384)`
    da gold, download bem maior e mais memória na indexação do que esta máquina tem de folga.
  - *`k` maior*: ganho marginal na validação, ao custo de mais texto no prompt; `k = 3` mantido.
  - *Expansão de consulta com sinônimos* (falta de ar → dispneia, grávida → gestante): não
    testada de propósito. O conjunto de validação foi escrito por quem faria a lista, e qualquer
    lista escrita agora sairia ajustada a ele; precisa de perguntas novas, de outra pessoa.

## Consequências

- Zero custo por consulta e nenhuma chave nova; a primeira execução baixa o modelo.
- Sem índice ANN (HNSW/IVFFlat): a busca é varredura exata. Com o volume de um dicionário de
  dados isso basta; se a tabela crescer, medir com `EXPLAIN ANALYZE` a consulta `_SQL_BUSCA`
  antes de criar índice.

## Limites conhecidos

- **Qualidade medida, não escrita aqui:** `make avaliar-rag` imprime, para cada conjunto, o
  acerto no 1º lugar e entre os 3 primeiros e as perguntas que erraram. Rodar logo depois de
  `make indexar` no banco real, nunca depois do `pytest` apontando para o mesmo banco (os testes
  reindexam `srag_teste` com texto falso).
- **Método contra sobreajuste:** dois conjuntos. `tests/dados/perguntas_rag.json` é o original
  (os erros dele motivaram a revisão). `tests/dados/perguntas_rag_validacao.json` foi escrito
  **antes** de qualquer mudança, em linguagem de usuário, com o campo esperado tirado do PDF e
  não da saída do modelo, cobrindo os campos das métricas e outros do dicionário. Cada mudança
  foi medida sozinha nos dois; só foi adotada a que melhorou a validação, e a busca por nome
  exato não mudou.
- **Limites honestos:** os conjuntos são pequenos, e diferença de uma ou duas perguntas é ruído
  (mudar só a pontuação do texto do trecho já mexe nisso; por isso o formato adotado é exatamente
  o que foi medido, e `rag/trechos.py` avisa que mudá-lo pede nova medição). A validação foi
  escrita por quem implementou a mudança: separa o ajuste da medida, mas não substitui perguntas
  de usuários reais. Sinônimo do dia a dia que não está no dicionário (falta de ar, grávida,
  morreu) continua sendo o ponto fraco do modelo pequeno; com o campo citado pelo nome a busca é
  exata.
- O arquivo do modelo é baixado pelo `fastembed` na primeira execução, sem revisão nem hash
  fixados no código: a versão baixada pode mudar entre máquinas. O pacote `fastembed` está travado no `uv.lock`; o arquivo do modelo não.
- O divisor depende do layout de tabela do PDF desta versão do dicionário e da detecção de tabela
  do PyMuPDF (travado no `uv.lock`); outra versão de qualquer um pede reconferência
  (`tests/unit/test_trechos.py` confere o PDF real).
