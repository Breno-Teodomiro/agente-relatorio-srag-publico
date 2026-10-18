-- Estrutura e permissões. Roda como admin, é idempotente.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS raw AUTHORIZATION srag_etl;
CREATE SCHEMA IF NOT EXISTS silver AUTHORIZATION srag_etl;
CREATE SCHEMA IF NOT EXISTS gold AUTHORIZATION srag_etl;
CREATE SCHEMA IF NOT EXISTS auditoria AUTHORIZATION srag_etl;

-- Ninguém além do ETL enxerga raw e silver.
REVOKE ALL ON SCHEMA raw, silver FROM PUBLIC;

-- TEMPORARY no banco vem de PUBLIC por padrão: com ele o agente criaria tabela temporária (esquema
-- pg_temp) fora do alcance dos GRANTs. Nenhum papel precisa: a carga usa COPY direto em raw e o
-- auditor só grava por coluna. CONNECT fica com PUBLIC de propósito (ADR 0010). O nome do banco
-- vem de current_database(), pois o mesmo arquivo roda no banco de produção e no de teste.
DO $$
BEGIN
  EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
END
$$;

-- Agente: só lê a gold, em sessão somente leitura e com tempo máximo por consulta.
GRANT USAGE ON SCHEMA gold TO srag_agente;
ALTER ROLE srag_agente SET default_transaction_read_only = on;
ALTER ROLE srag_agente SET statement_timeout = '5s';
ALTER DEFAULT PRIVILEGES FOR ROLE srag_etl IN SCHEMA gold
  GRANT SELECT ON TABLES TO srag_agente;

-- Auditor: lê a auditoria; INSERT e UPDATE só por coluna, concedidos em 04_auditoria.sql (sem
-- DELETE: o registro não se apaga). Tabela nova em auditoria nasce sem nenhum privilégio.
-- ALTER DEFAULT PRIVILEGES só acrescenta; o REVOKE limpa concessões de versões anteriores.
GRANT USAGE ON SCHEMA auditoria TO srag_auditor;
ALTER DEFAULT PRIVILEGES FOR ROLE srag_etl IN SCHEMA auditoria
  REVOKE ALL ON TABLES FROM srag_auditor;
ALTER DEFAULT PRIVILEGES FOR ROLE srag_etl IN SCHEMA auditoria
  GRANT SELECT ON TABLES TO srag_auditor;
ALTER DEFAULT PRIVILEGES FOR ROLE srag_etl IN SCHEMA auditoria
  GRANT USAGE ON SEQUENCES TO srag_auditor;
