-- Silver: só as colunas que alguma métrica usa, tipadas e limpas. Sem identificadores.
-- Idempotente: recria tudo a cada execução. O DROP ... CASCADE remove as views da gold,
-- que o 03_gold.sql recria na mesma transação (ver transformar.py).
-- Na raw, vazio chega como '' (texto) e não como NULL: toda regra trata os dois.

CREATE OR REPLACE FUNCTION silver.para_data(valor text, minimo date, maximo date)
RETURNS date LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  d date;
BEGIN
  -- Aceita 'AAAA-MM-DD' e 'AAAA-MM-DD hh:mm:ss.nnnnnnnnn' (há os dois formatos na base).
  -- Sem limites (raw sem DT_NOTIFIC válida) nenhuma data passa: falha fechada.
  IF valor IS NULL OR valor !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
     OR minimo IS NULL OR maximo IS NULL THEN
    RETURN NULL;
  END IF;
  d := substr(valor, 1, 10)::date;
  IF d < minimo OR d > maximo THEN
    RETURN NULL;
  END IF;
  RETURN d;
EXCEPTION WHEN others THEN
  RETURN NULL;  -- dia que não existe (ex.: 2025-02-30) vira NULL sem derrubar a carga
END $$;

CREATE OR REPLACE FUNCTION silver.sim_nao(valor text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE valor WHEN '1' THEN 'sim' WHEN '2' THEN 'nao' ELSE 'ignorado' END
$$;

CREATE OR REPLACE FUNCTION silver.uf_valida(valor text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT valor = ANY (ARRAY['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT',
    'PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO'])
$$;

-- Idade em anos completos. TP_IDADE: 1 = dias, 2 = meses, 3 = anos. Fora de 0–120 → NULL.
-- A base real tem idade em meses acima de 11 (ex.: 63 meses = 5 anos): converter, não zerar.
-- Com até 3 dígitos, dias (÷365) e meses (÷12) nunca passam de 120 anos.
-- Um CASE só, sem FROM: assim o Postgres expande a função na consulta (inlining).
CREATE OR REPLACE FUNCTION silver.idade_anos(n text, tp text) RETURNS int
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN n IS NULL OR n !~ '^[0-9]{1,3}$' THEN NULL
    WHEN tp = '1' THEN n::int / 365
    WHEN tp = '2' THEN n::int / 12
    WHEN tp = '3' AND n::int <= 120 THEN n::int
  END
$$;

-- plpgsql de propósito: a versão em SQL com subconsulta não é expandida (inlining) e roda
-- o executor de função a cada linha, ordens de grandeza mais lenta na base real.
CREATE OR REPLACE FUNCTION silver.faixa_etaria(n text, tp text) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  a int := silver.idade_anos(n, tp);
BEGIN
  RETURN CASE
    WHEN a IS NULL THEN 'ignorado'
    WHEN a < 1 THEN '<1'
    WHEN a <= 4 THEN '1-4'
    WHEN a <= 11 THEN '5-11'
    WHEN a <= 17 THEN '12-17'
    WHEN a <= 39 THEN '18-39'
    WHEN a <= 59 THEN '40-59'
    ELSE '60+'
  END;
END $$;

-- Janela de datas aceitas, derivada da própria base e robusta a notificação absurda:
-- 1. notificações plausíveis = a até 365 dias da mediana das DT_NOTIFIC válidas;
-- 2. janela = [menor plausível − 365 dias, maior plausível].
-- Sem o passo 1, uma única DT_NOTIFIC de 1695 ou 2202 alargaria a janela de todas as datas
-- e moveria o "hoje" (máx. DT_SIN_PRI). Sem notificação válida, tudo NULL (falha fechada).
-- DT_NOTIFIC passa pela mesma leitura das outras datas (prefixo, dia inexistente → NULL).
DROP TABLE IF EXISTS silver.limites CASCADE;
CREATE TABLE silver.limites AS
WITH notificacoes AS (
  SELECT d
  FROM (
    SELECT silver.para_data(dt_notific, '-infinity'::date, 'infinity'::date) AS d
    FROM raw.srag
  ) AS lidas
  WHERE d IS NOT NULL
),
referencia AS (
  -- percentile_disc devolve uma data que existe na base (mediana inferior se a contagem é par).
  SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY d) AS mediana FROM notificacoes
),
classificadas AS (
  SELECT n.d, n.d BETWEEN r.mediana - 365 AND r.mediana + 365 AS plausivel
  FROM notificacoes AS n CROSS JOIN referencia AS r
)
SELECT
  min(d) FILTER (WHERE plausivel) - 365 AS minimo,
  max(d) FILTER (WHERE plausivel) AS maximo,
  (SELECT mediana FROM referencia) AS mediana,
  count(*) FILTER (WHERE NOT plausivel)::int AS notificacoes_atipicas
FROM classificadas;

DROP TABLE IF EXISTS silver.casos CASCADE;
CREATE TABLE silver.casos AS
SELECT
  row_number() OVER (ORDER BY r.dt_sin_pri, r.nu_notific)::int AS id_caso,
  CASE WHEN silver.uf_valida(r.sg_uf_not) THEN r.sg_uf_not END AS uf,
  silver.faixa_etaria(r.nu_idade_n, r.tp_idade) AS faixa_etaria,
  silver.para_data(r.dt_notific, l.minimo, l.maximo) AS dt_notificacao,
  silver.para_data(r.dt_sin_pri, l.minimo, l.maximo) AS dt_sintomas,
  silver.para_data(r.dt_interna, l.minimo, l.maximo) AS dt_internacao,
  silver.para_data(r.dt_entuti, l.minimo, l.maximo) AS dt_entrada_uti,
  silver.para_data(r.dt_evoluca, l.minimo, l.maximo) AS dt_evolucao,
  silver.sim_nao(r.hospital) AS hospitalizado,
  silver.sim_nao(r.uti) AS uti,
  CASE r.suport_ven
    WHEN '1' THEN 'invasivo' WHEN '2' THEN 'nao_invasivo' WHEN '3' THEN 'nao'
    ELSE 'ignorado'
  END AS suporte_ventilatorio,
  CASE
    WHEN nullif(trim(r.evolucao), '') IS NULL THEN 'sem_desfecho'
    WHEN r.evolucao = '1' THEN 'cura'
    WHEN r.evolucao = '2' THEN 'obito'
    WHEN r.evolucao = '3' THEN 'obito_outras_causas'
    ELSE 'ignorado'
  END AS evolucao,
  CASE
    WHEN nullif(trim(r.classi_fin), '') IS NULL THEN 'em_investigacao'
    WHEN r.classi_fin = '1' THEN 'influenza'
    WHEN r.classi_fin = '2' THEN 'outro_virus'
    WHEN r.classi_fin = '3' THEN 'outro_agente'
    WHEN r.classi_fin = '4' THEN 'nao_especificado'
    WHEN r.classi_fin = '5' THEN 'covid19'
    ELSE 'ignorado'
  END AS classificacao,
  silver.sim_nao(r.vacina) AS vacina_gripe,
  silver.sim_nao(r.vacina_cov) AS vacina_covid
FROM raw.srag AS r
CROSS JOIN silver.limites AS l;

ALTER TABLE silver.casos ADD PRIMARY KEY (id_caso);
CREATE INDEX ON silver.casos (dt_sintomas);
CREATE INDEX ON silver.casos (uf);
