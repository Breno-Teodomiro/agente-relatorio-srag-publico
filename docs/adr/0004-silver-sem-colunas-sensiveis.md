# ADR 0004 — A silver não leva colunas sensíveis nem sem uso

- **Status:** aceita (14/10/2026, spec §5)

## Contexto

Dado de saúde, raça e etnia é dado pessoal sensível (LGPD art. 5º, II). A base é pública e sem
nome, mas nascimento + município + sexo + datas permitem reidentificar. Nenhuma das 4 métricas
precisa dessas colunas.

## Decisão

- A `raw` guarda o CSV como veio (só o papel `srag_etl` enxerga). A passagem raw → silver
  (`dados/sql/01_silver.sql`) **não leva**: `NU_NOTIFIC`, `DT_NASC`, todos os municípios e
  unidades, `CS_RACA`, `CS_ETINIA`, `CS_ESCOL_N`, `PAC_COCBO`, `PAC_DSCBO`, lotes de vacina,
  `TEM_CPF`, `POV_CT`, `ESTRANG`, `CS_SEXO`, sintomas e textos livres.
- Fica: UF de notificação, faixa etária (idade vira faixa), datas de notificação, de início dos sintomas,
  internação, UTI e evolução, e os campos das métricas (`HOSPITAL`, `UTI`, `SUPORT_VEN`,
  `EVOLUCAO`, `CLASSI_FIN`, `VACINA`, `VACINA_COV`).
- `NU_NOTIFIC` vira uma chave interna (`id_caso`) gerada na carga.
- A lista final com o motivo de cada coluna fica em `docs/DICIONARIO_SILVER.md`.

## Alternativas consideradas

- **Levar tudo e restringir por permissão:** descartada; minimização (LGPD art. 6º, III) é não
  ter o dado na camada, não só escondê-lo.
- **Pseudonimizar município:** descartada; nenhuma métrica usa, e o código ainda permitiria
  cruzamento.

## Consequências

- O agente não tem como citar município, sexo ou raça, nem por engano: o dado não existe na gold.
- Análise por município ou sexo exigiria nova decisão (ADR novo) e nova coluna na silver.

## Limites conhecidos

- `id_caso` é pseudônimo reversível por quem tem acesso à raw (é a posição do caso ordenado por
  `DT_SIN_PRI` e `NU_NOTIFIC`); a proteção é o papel do banco, não o número em si. O agente nem
  o vê: a gold só tem agregados.
- A raw continua no banco local com tudo; quem protege é o `REVOKE` do schema (ADR 0010).
- Seis colunas da silver não são lidas por nenhuma view da gold (`dt_notificacao`,
  `dt_internacao`, `dt_entrada_uti`, `dt_evolucao`, `hospitalizado`, `suporte_ventilatorio`).
  Pelo critério de minimização poderiam sair; ficam porque `dt_notificacao` alimenta a janela de
  datas válidas (`silver.limites`) e as demais são datas e indicadores não sensíveis que as
  métricas futuras usariam. Remover exige mexer na silver, na gold e nos testes (risco de
  regressão); a lista completa e o uso de cada coluna estão em `docs/DICIONARIO_SILVER.md`.
