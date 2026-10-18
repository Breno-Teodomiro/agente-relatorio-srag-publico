# ADR 0002 — "Taxa de UTI" é proporção de casos internados em UTI

- **Status:** aceita (14/10/2026, spec §4)

## Contexto

O desafio pede uma "taxa de ocupação de UTI". A base SRAG traz, por caso, se ele foi para a UTI
(`UTI`), mas não traz leitos nem capacidade. Ocupação = leitos ocupados ÷ leitos existentes, e o
denominador não existe nesta base.

## Decisão

- A métrica `uti` (`metricas/catalogo.py`) é `UTI = 1` ÷ `UTI ∈ {1, 2}`: proporção de casos de
  SRAG internados em UTI entre os que têm a informação preenchida.
- A limitação sai **no próprio relatório**, junto do número: "Não é a taxa de ocupação de leitos:
  a base não traz a capacidade de UTI. A ocupação real exigiria cruzar com o cadastro de leitos do
  CNES."
- Casos sem a informação (vazio/ignorado) vão para `excluidos`, publicados ao lado.

## Alternativas consideradas

- **Chamar de "ocupação" mesmo assim:** descartada; seria número com nome errado.
- **Cruzar com o CNES agora:** descartada para a PoC (carga nova, outra granularidade, sem ganho
  nos 5 critérios). Fica como evolução: carga nova + view + entrada no catálogo.

## Consequências

- O leitor não confunde a métrica com lotação hospitalar; o guardrail de saída só deixa o LLM
  citar o número que veio da ferramenta.

## Limites conhecidos

- Preenchimento de `UTI` varia entre UFs; a taxa vale para quem preencheu.
