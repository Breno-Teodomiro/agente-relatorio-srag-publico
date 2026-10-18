# ADR 0008 — Mortalidade, UTI e vacinação sobre todo o período da base

- **Status:** aceita (14/10/2026, spec §4)

## Contexto

Mortalidade depende de desfecho, e o desfecho chega semanas depois do início dos sintomas. Numa
janela curta e recente, a maioria dos casos ainda está "sem desfecho", e a taxa fica enviesada.

## Decisão

- `mortalidade`, `uti` e `vacinacao` são calculadas sobre **todo o período da base** (temporada
  até a data de referência), pela view `gold.indicadores` (`dados/sql/03_gold.sql`).
- O período (`periodo_inicio`, `periodo_fim`) sai junto de cada métrica no relatório.
- Só a taxa de aumento usa janela curta (7 × 7 dias consolidados, ADR 0003).
- Cada métrica devolve numerador, denominador e excluídos (vazio, ignorado, sem desfecho).

## Alternativas consideradas

- **Últimos 30 dias para todas:** descartada; mortalidade subestimada por falta de desfecho.
- **Janela móvel com corte de consolidação também nas taxas:** descartada para a PoC; mais uma
  regra para explicar, sem ganho nos critérios de avaliação.

## Consequências

- As três taxas são estáveis e comparáveis entre execuções da mesma base; não respondem "o que
  mudou na última semana" (quem responde é a taxa de aumento e o gráfico diário).

## Limites conhecidos

- Casos recentes sem desfecho ficam fora do denominador da mortalidade; se o desfecho dos
  recentes for diferente dos antigos, a taxa ainda carrega esse viés (menor).
- Vacinação é **entre os internados por SRAG**, não cobertura da população (dito na limitação).
