# ADR 0003 — Números só do SQL da gold; "hoje" = data máxima de sintomas; consolidação de 14 dias

- **Status:** aceita (14/10/2026, spec §3 e §4)

## Contexto

LLM erra conta e arredonda do jeito errado. A base é um retrato fechado (vai até jun/2025), então
"hoje" não pode ser a data do relógio. E os dias mais recentes ainda recebem notificações
atrasadas: sem corte, toda série termina numa queda que não aconteceu.

## Decisão

- **Todo número do relatório vem do SQL** (views da camada `gold`, `dados/sql/03_gold.sql`), lido
  por `metricas/repositorio.py` e montado em `metricas/catalogo.py`/`series.py`. O LLM só comenta.
  O guardrail de saída confere cada número citado contra as evidências (ADR 0005).
- **"Hoje" = data de referência = `max(dt_sintomas)`** (`gold.referencia`), isto é, o máximo de
  `DT_SIN_PRI` válido na silver. A data de cada caso é `DT_SIN_PRI`.
- **Consolidação de 14 dias:** `data_consolidada = data_referencia − 14`. A taxa de aumento compara
  os 7 dias consolidados mais recentes com os 7 anteriores (`gold.janelas_aumento`); os casos dos
  14 dias recentes saem como `excluidos` ("em consolidação"). No gráfico diário, esses dias
  aparecem destacados com legenda.
- A janela de datas plausíveis é robusta a outlier (mediana de `DT_NOTIFIC` ± 365 dias, spec §5.1):
  uma notificação absurda não move o "hoje".

## Alternativas consideradas

- **LLM calcula a partir de linhas:** descartada (erro de conta, dado sensível no prompt).
- **"Hoje" = data da execução:** descartada; a série ficaria vazia ou cortada.
- **Sem corte de consolidação:** descartada; criaria queda falsa. O valor de 14 dias vem do
  spec; não foi estimado a partir do atraso medido nesta base.

## Consequências

- Mesma base → mesmos números, com ou sem LLM. O relatório é reprodutível e a auditoria guarda a
  data de referência e a contagem da silver de cada execução.
- A taxa de aumento olha um período que termina 14 dias antes da referência; isso é dito na
  limitação da métrica.

## Limites conhecidos

- O corte de 14 dias é fixo (não estimado por UF nem por semana); atraso maior ainda distorce.
- Conferir que um número "existe nas evidências" não garante que ele foi citado para a coisa
  certa (limitação declarada em `guardrails/saida.py`).

## Como conferir

`tests/integracao/test_gold.py` (janelas conferidas à mão e contra o oráculo),
`tests/integracao/test_silver.py` (notificação absurda não move o "hoje"); `make testes`.
