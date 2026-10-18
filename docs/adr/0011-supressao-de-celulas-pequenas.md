# ADR 0011 — Supressão de células pequenas: `<5`, complementar `oculto` e por janelas publicadas

- **Status:** aceita (spec §5, proteção 2; regra complementar e por janelas decididas nas
  revisões da Task 6; registrada em 17/10/2026)

## Contexto

Contagem pequena de casos por UF, dia ou categoria ajuda a reidentificar pessoas. A regra
inicial ("1 a 4 vira `<5`") não basta quando o relatório também publica o **total exato**: com
uma única célula `<5`, total − exibidas devolve o valor. E a mesma página publica outras somas
exatas sobre os mesmos dias (semanas do aumento de casos, dias em consolidação, barras mensais),
que abrem o mesmo canal por subtração.

## Decisão

1. **Primária** (`metricas/supressao.py`): contagem de 1 a 4 sai `"<5"`. Zero não se suprime;
   `None` (fora da base) passa.
2. **Complementar dentro de uma resposta** (`suprimir_celulas`): havendo algum `"<5"`, a menor
   célula com 5 ou mais sai `"oculto"` (no empate, a primeira na ordem). Se, para quem conhece a
   regra e o total, algum `"<5"` ainda ficar com um só valor possível por intervalo, sai a
   próxima menor, até nenhum ficar determinado ou acabarem as células com 5 ou mais. O total da
   resposta continua exato; o percentual de célula escondida é nulo (`calcular_distribuicao`).
3. **Por janelas publicadas na série diária** (`janelas_publicadas` + `suprimir_com_janelas`,
   `metricas/series.py`): as somas exatas que tocam os 30 dias são a semana atual e a anterior do
   aumento de casos, os dias em consolidação e cada mês que cabe inteiro nos 30 dias. Os dias são
   agrupados em blocos (mesma combinação de janelas) e a complementar roda **dentro de cada
   bloco**. Como toda soma publicada é soma de blocos inteiros, se nenhum `"<5"` fica determinado
   para quem soubesse a soma de cada bloco, não fica para o leitor real.
4. **Série mensal** (`_suprimir_mensal`): mês que cabe inteiro na série diária, com 5 ou mais,
   nunca é usado como `"oculto"` (as barras diárias o devolveriam); a complementar roda nos
   demais meses.
5. **Métricas** (`metricas/catalogo.py`): se numerador, denominador ou a diferença entre eles
   fica entre 1 e 4, o valor sai nulo, as contagens mascaradas e o complemento vazio; `excluidos`
   passa sempre pela primária.
6. **Leitura:** `"<5"` e `"oculto"` não viram barra zero (marca própria e legenda no gráfico);
   cada série e distribuição leva a nota explicando a regra; os prompts proíbem inferir o valor e
   o guardrail de saída barra contagem de 1 a 4 no texto.

## Alternativas consideradas

- **Só a primária:** descartada; vaza por subtração do total.
- **Arredondar ou suprimir o total:** descartada; percentual e excluídos devolvem o total, e o
  total exato é regra do relatório.
- **Ruído (privacidade diferencial):** descartada; os números deixariam de ser os do SQL e o
  guardrail de saída não teria o que conferir.

## Consequências

- Os números continuam exatos onde podem; o leitor vê claramente o que foi escondido e por quê.
- Toda nova soma publicada sobre os mesmos dias passa a ser parte da regra (limite 3).

## Limites conhecidos

1. **Bloco só com zeros e `"<5"`** não tem o que esconder. Se a soma do bloco fixar um `"<5"`
   (um `"<5"` entre zeros; dois `"<5"` que somam 8), só mascarar o agregado da janela onde ele é
   publicado protegeria. O mesmo vale numa resposta sem célula com 5 ou mais (`[4, 4, 4]` com
   total 12). Some quando a própria janela publicada já sai `"<5"`.
2. **Bloco de um dia criado pelo corte do mês corrente.** Quando o dia do mês da data de
   referência é **13, 15, 20, 22, 27 ou 29**, um dia fica sozinho num bloco e sai direto da
   diferença entre somas publicadas (numerador, excluídos, barra do mês), sem olhar a série
   diária. A base atual (referência em 25/06/2025) não é afetada. Correção proposta: o catálogo
   usar `janelas_publicadas` e mascarar numerador, denominador e excluídos quando a janela isolar
   uma célula suprimida. Simulando `janelas_publicadas` para todos os dias do mês, também o
   **dia 1** cria bloco de um dia (o próprio dia de referência é o mês corrente inteiro), e uma
   referência em 1º de março, depois de fevereiro com 28 dias, cria três. Isso ainda não está na
   docstring de `series.py`.
3. **Toda métrica nova baseada em dias** (ou mudança das janelas em `03_gold.sql`) precisa entrar
   em `janelas_publicadas`. O teste `test_janelas_da_serie_somam_o_mesmo_que_a_gold`
   (`tests/integracao/test_series.py`) confere só as janelas atuais.
4. **O total exato da série diária** segue no JSON da ferramenta, no que o LLM lê, na auditoria e
   no JSONL. A regra parte do princípio de que ele é público; se outro canal publicar uma soma
   parcial nova, a premissa quebra (volta ao limite 3).
5. **A própria decisão de parar** a supressão iterativa pode vazar um pouco para quem conhece o
   algoritmo. Foi visto em listas sintéticas na Task 6, não na gold real da época.
6. **Canais entre respostas** não são fechados: Brasil menos a soma das UFs, cada uma com a sua
   supressão. Montar isso exige 28 chamadas (Brasil + 27 UFs), acima do teto de 12 por execução
   (ADR 0005), mas execuções separadas somam. Aceito porque os microdados originais são públicos
   no Open DATASUS: a supressão protege o relatório, não substitui a proteção da fonte.

## Como conferir

`tests/unit/test_supressao.py` (inclui teste de propriedade), `tests/unit/test_series.py`,
`tests/unit/test_catalogo.py`, `tests/integracao/test_series.py`; `make testes`. Blocos de um dia
para uma data de referência `ref` (com `PYTHONPATH=src`):

```python
from datetime import timedelta
from srag_agente.metricas.series import janelas_publicadas
dias = [ref - timedelta(days=29 - i) for i in range(30)]
janelas = list(janelas_publicadas(dias, ref - timedelta(days=14)).values())
blocos = {}
for p in range(30):
    blocos.setdefault(tuple(p in j for j in janelas), []).append(dias[p])
print([b for b in blocos.values() if len(b) == 1])
```
