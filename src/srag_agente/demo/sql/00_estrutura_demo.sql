-- Estrutura do banco da demonstração. Roda como o dono do banco (não superusuário), idempotente,
-- dentro da transação da carga (`srag demo carregar`).
-- Diferenças para dados/sql/00_estrutura.sql: sem raw e silver, sem o papel de carga, e sem
-- privilégio padrão (a carga concede tabela por tabela, depois de criá-las).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS auditoria;
REVOKE ALL ON SCHEMA gold, auditoria FROM PUBLIC;

-- Ninguém cria objeto fora dos esquemas da demo: nem em public, nem em tabela temporária
-- (pg_temp fica fora do alcance dos GRANTs). CONNECT continua com PUBLIC (ADR 0010).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
DO $$
BEGIN
  EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
END
$$;

-- Configuração de sessão dos papéis volta ao esperado a cada carga: o próprio papel consegue
-- `ALTER ROLE <si> SET ...` e a mudança ficaria (auditor em REPEATABLE READ cairia no teto em todo
-- INSERT; agente com statement_timeout = 0 ficaria sem tempo máximo). Geral e deste banco.
ALTER ROLE srag_agente RESET ALL;
ALTER ROLE srag_auditor RESET ALL;
DO $$
BEGIN
  EXECUTE format('ALTER ROLE srag_agente IN DATABASE %I RESET ALL', current_database());
  EXECUTE format('ALTER ROLE srag_auditor IN DATABASE %I RESET ALL', current_database());
END
$$;

-- Agente: só lê a gold, em sessão somente leitura e com tempo máximo por consulta.
GRANT USAGE ON SCHEMA gold TO srag_agente;
ALTER ROLE srag_agente SET default_transaction_read_only = on;
ALTER ROLE srag_agente SET statement_timeout = '5s';  -- o mesmo valor de repositorio.TEMPO_MAXIMO

-- Auditor: só a auditoria; os privilégios por coluna vêm de dados/sql/04_auditoria.sql.
GRANT USAGE ON SCHEMA auditoria TO srag_auditor;
