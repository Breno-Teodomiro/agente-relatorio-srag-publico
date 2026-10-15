-- Gold: somente agregados. É a única camada que o agente enxerga.
-- As views dependem de silver.casos; o DROP ... CASCADE da silver as remove e este arquivo as
-- recria (transformar.py recusa rodar o 01 sem este arquivo depois, na mesma transação).
-- Em toda view, uf = 'BR' é o Brasil inteiro e uf = 'ND' é UF inválida (nula na silver).
-- As views rodam com o privilégio do dono (srag_etl): o agente lê o agregado sem ler a silver.

CREATE OR REPLACE VIEW gold.referencia AS
SELECT
  max(dt_sintomas) AS data_referencia,
  max(dt_sintomas) - 14 AS data_consolidada,
  min(dt_sintomas) AS inicio_base,
  count(*)::int AS total_casos
FROM silver.casos;

CREATE OR REPLACE VIEW gold.casos_diarios AS
SELECT
  CASE WHEN GROUPING(uf) = 1 THEN 'BR' ELSE coalesce(uf, 'ND') END AS uf,
  dt_sintomas AS dia,
  count(*)::int AS casos
FROM silver.casos
WHERE dt_sintomas IS NOT NULL
GROUP BY GROUPING SETS ((uf, dt_sintomas), (dt_sintomas));

CREATE OR REPLACE VIEW gold.indicadores AS
SELECT
  CASE WHEN GROUPING(uf) = 1 THEN 'BR' ELSE coalesce(uf, 'ND') END AS uf,
  count(*)::int AS total_casos,
  count(*) FILTER (WHERE evolucao = 'obito')::int AS obitos,
  count(*) FILTER (WHERE evolucao IN ('cura', 'obito', 'obito_outras_causas'))::int AS com_desfecho,
  count(*) FILTER (WHERE uti = 'sim')::int AS uti_sim,
  count(*) FILTER (WHERE uti IN ('sim', 'nao'))::int AS uti_informada,
  count(*) FILTER (WHERE vacina_covid = 'sim')::int AS vacina_covid_sim,
  count(*) FILTER (WHERE vacina_covid IN ('sim', 'nao'))::int AS vacina_covid_informada,
  count(*) FILTER (WHERE vacina_gripe = 'sim')::int AS vacina_gripe_sim,
  count(*) FILTER (WHERE vacina_gripe IN ('sim', 'nao'))::int AS vacina_gripe_informada,
  min(dt_sintomas) AS periodo_inicio,
  max(dt_sintomas) AS periodo_fim
FROM silver.casos
GROUP BY GROUPING SETS ((uf), ());

CREATE OR REPLACE VIEW gold.janelas_aumento AS
SELECT
  d.uf,
  coalesce(sum(d.casos) FILTER (
    WHERE d.dia BETWEEN r.data_consolidada - 6 AND r.data_consolidada), 0)::int AS casos_7d_atual,
  coalesce(sum(d.casos) FILTER (
    WHERE d.dia BETWEEN r.data_consolidada - 13 AND r.data_consolidada - 7), 0)::int
    AS casos_7d_anterior,
  coalesce(sum(d.casos) FILTER (
    WHERE d.dia BETWEEN r.data_consolidada - 29 AND r.data_consolidada), 0)::int AS casos_30d_atual,
  coalesce(sum(d.casos) FILTER (
    WHERE d.dia BETWEEN r.data_consolidada - 59 AND r.data_consolidada - 30), 0)::int
    AS casos_30d_anterior,
  coalesce(sum(d.casos) FILTER (WHERE d.dia > r.data_consolidada), 0)::int AS casos_em_consolidacao,
  r.data_consolidada
FROM gold.casos_diarios AS d
CROSS JOIN gold.referencia AS r
GROUP BY d.uf, r.data_consolidada;

CREATE OR REPLACE VIEW gold.distribuicao AS
SELECT
  'classificacao' AS dimensao,
  CASE WHEN GROUPING(uf) = 1 THEN 'BR' ELSE coalesce(uf, 'ND') END AS uf,
  classificacao AS categoria,
  count(*)::int AS casos
FROM silver.casos
GROUP BY GROUPING SETS ((uf, classificacao), (classificacao))
UNION ALL
SELECT
  'faixa_etaria',
  CASE WHEN GROUPING(uf) = 1 THEN 'BR' ELSE coalesce(uf, 'ND') END,
  faixa_etaria,
  count(*)::int
FROM silver.casos
GROUP BY GROUPING SETS ((uf, faixa_etaria), (faixa_etaria));

-- Trechos do dicionário de dados para o RAG (não depende da silver; sobrevive a recargas).
-- Única tabela da gold: guarda texto de documento público do DATASUS, nunca linha da base.
CREATE TABLE IF NOT EXISTS gold.dicionario_trechos (
  id serial PRIMARY KEY,
  campo text,
  pagina int NOT NULL,
  texto text NOT NULL,
  embedding vector(384) NOT NULL
);

-- Os privilégios padrão (00_estrutura.sql) já dão SELECT ao agente em objeto novo do ETL;
-- o GRANT explícito cobre objeto criado antes deles. Só leitura: escrita fica com o dono (ETL).
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO srag_agente;
