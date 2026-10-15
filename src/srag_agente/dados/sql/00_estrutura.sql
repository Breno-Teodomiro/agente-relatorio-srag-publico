-- Estrutura e permissões. Roda como admin, é idempotente.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS raw AUTHORIZATION srag_etl;
CREATE SCHEMA IF NOT EXISTS silver AUTHORIZATION srag_etl;
CREATE SCHEMA IF NOT EXISTS gold AUTHORIZATION srag_etl;
CREATE SCHEMA IF NOT EXISTS auditoria AUTHORIZATION srag_etl;

-- Ninguém além do ETL enxerga raw e silver.
REVOKE ALL ON SCHEMA raw, silver FROM PUBLIC;

-- Agente: só lê a gold, em sessão somente leitura e com tempo máximo por consulta.
GRANT USAGE ON SCHEMA gold TO srag_agente;
ALTER ROLE srag_agente SET default_transaction_read_only = on;
ALTER ROLE srag_agente SET statement_timeout = '5s';
ALTER DEFAULT PRIVILEGES FOR ROLE srag_etl IN SCHEMA gold
  GRANT SELECT ON TABLES TO srag_agente;

-- Auditor: só grava e lê a auditoria (sem UPDATE/DELETE: o registro não se reescreve).
-- ALTER DEFAULT PRIVILEGES só acrescenta; o REVOKE limpa concessões de versões anteriores.
GRANT USAGE ON SCHEMA auditoria TO srag_auditor;
ALTER DEFAULT PRIVILEGES FOR ROLE srag_etl IN SCHEMA auditoria
  REVOKE ALL ON TABLES FROM srag_auditor;
ALTER DEFAULT PRIVILEGES FOR ROLE srag_etl IN SCHEMA auditoria
  GRANT SELECT, INSERT ON TABLES TO srag_auditor;
ALTER DEFAULT PRIVILEGES FOR ROLE srag_etl IN SCHEMA auditoria
  GRANT USAGE ON SEQUENCES TO srag_auditor;
