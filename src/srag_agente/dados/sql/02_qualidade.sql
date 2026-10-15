-- Quantas linhas cada regra de limpeza afetou. Uma passada só pela raw.
-- Vazio ('' ou NULL) não conta como erro: vira "ignorado"/"sem desfecho" por regra própria.
-- Exceção: UF vazia conta em uf_invalida, porque vira NULL na silver como a UF inválida.
DROP TABLE IF EXISTS silver.qualidade CASCADE;
CREATE TABLE silver.qualidade AS
WITH contagens AS (
  SELECT
    count(*) AS total_linhas,
    count(*) FILTER (WHERE nullif(trim(dt_notific), '') IS NOT NULL
      AND silver.para_data(dt_notific, minimo, maximo) IS NULL) AS dt_notific,
    count(*) FILTER (WHERE nullif(trim(dt_sin_pri), '') IS NOT NULL
      AND silver.para_data(dt_sin_pri, minimo, maximo) IS NULL) AS dt_sin_pri,
    count(*) FILTER (WHERE nullif(trim(dt_interna), '') IS NOT NULL
      AND silver.para_data(dt_interna, minimo, maximo) IS NULL) AS dt_interna,
    count(*) FILTER (WHERE nullif(trim(dt_entuti), '') IS NOT NULL
      AND silver.para_data(dt_entuti, minimo, maximo) IS NULL) AS dt_entuti,
    count(*) FILTER (WHERE nullif(trim(dt_evoluca), '') IS NOT NULL
      AND silver.para_data(dt_evoluca, minimo, maximo) IS NULL) AS dt_evoluca,
    count(*) FILTER (WHERE nullif(trim(nu_idade_n), '') IS NOT NULL
      AND silver.idade_anos(nu_idade_n, tp_idade) IS NULL) AS nu_idade_n,
    count(*) FILTER (WHERE NOT coalesce(silver.uf_valida(sg_uf_not), false)) AS sg_uf_not,
    count(*) FILTER (WHERE nullif(trim(hospital), '') IS NOT NULL
      AND hospital NOT IN ('1', '2', '9')) AS hospital,
    count(*) FILTER (WHERE nullif(trim(uti), '') IS NOT NULL
      AND uti NOT IN ('1', '2', '9')) AS uti,
    count(*) FILTER (WHERE nullif(trim(suport_ven), '') IS NOT NULL
      AND suport_ven NOT IN ('1', '2', '3', '9')) AS suport_ven,
    count(*) FILTER (WHERE nullif(trim(evolucao), '') IS NOT NULL
      AND evolucao NOT IN ('1', '2', '3', '9')) AS evolucao,
    count(*) FILTER (WHERE nullif(trim(classi_fin), '') IS NOT NULL
      AND classi_fin NOT IN ('1', '2', '3', '4', '5')) AS classi_fin,
    count(*) FILTER (WHERE nullif(trim(vacina), '') IS NOT NULL
      AND vacina NOT IN ('1', '2', '9')) AS vacina,
    count(*) FILTER (WHERE nullif(trim(vacina_cov), '') IS NOT NULL
      AND vacina_cov NOT IN ('1', '2', '9')) AS vacina_cov
  FROM raw.srag CROSS JOIN silver.limites
)
SELECT v.regra, v.coluna, v.linhas_afetadas::int, c.total_linhas::int
FROM contagens AS c
CROSS JOIN LATERAL (VALUES
  ('data_invalida_ou_fora_do_periodo', 'dt_notific', c.dt_notific),
  ('data_invalida_ou_fora_do_periodo', 'dt_sin_pri', c.dt_sin_pri),
  ('data_invalida_ou_fora_do_periodo', 'dt_interna', c.dt_interna),
  ('data_invalida_ou_fora_do_periodo', 'dt_entuti', c.dt_entuti),
  ('data_invalida_ou_fora_do_periodo', 'dt_evoluca', c.dt_evoluca),
  ('idade_invalida', 'nu_idade_n', c.nu_idade_n),
  ('uf_invalida', 'sg_uf_not', c.sg_uf_not),
  ('codigo_fora_do_dominio', 'hospital', c.hospital),
  ('codigo_fora_do_dominio', 'uti', c.uti),
  ('codigo_fora_do_dominio', 'suport_ven', c.suport_ven),
  ('codigo_fora_do_dominio', 'evolucao', c.evolucao),
  ('codigo_fora_do_dominio', 'classi_fin', c.classi_fin),
  ('codigo_fora_do_dominio', 'vacina', c.vacina),
  ('codigo_fora_do_dominio', 'vacina_cov', c.vacina_cov)
) AS v(regra, coluna, linhas_afetadas);
