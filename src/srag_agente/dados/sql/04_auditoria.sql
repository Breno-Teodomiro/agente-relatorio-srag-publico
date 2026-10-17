-- Trilha de auditoria do agente. Nunca é apagada pelas recargas (só CREATE ... IF NOT EXISTS).
-- Guarda só agregados e metadados da execução, nunca linhas da base (spec §5, proteção 3).
CREATE TABLE IF NOT EXISTS auditoria.execucoes (
  id_execucao uuid PRIMARY KEY,
  inicio timestamptz NOT NULL DEFAULT now(),
  fim timestamptz,
  status text NOT NULL DEFAULT 'em_andamento',
  provedor text NOT NULL,
  modelo text NOT NULL,
  parametros jsonb NOT NULL,
  versao_codigo text NOT NULL,
  hash_prompts text NOT NULL,
  data_referencia date,
  total_casos int,  -- contagem da silver que a execução viu (spec §7)
  tokens_entrada int NOT NULL DEFAULT 0,
  tokens_saida int NOT NULL DEFAULT 0,
  custo_usd numeric(10, 6) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS auditoria.eventos (
  id bigserial PRIMARY KEY,
  id_execucao uuid NOT NULL REFERENCES auditoria.execucoes,
  sequencia int NOT NULL,
  momento timestamptz NOT NULL DEFAULT now(),
  no text NOT NULL,
  tipo text NOT NULL CHECK (tipo IN ('llm', 'ferramenta', 'guardrail', 'decisao')),
  ferramenta text,
  argumentos jsonb,
  motivo text,
  resultado jsonb,
  duracao_ms int,
  tokens_entrada int,
  tokens_saida int,
  erro text,
  UNIQUE (id_execucao, sequencia)
);

-- Ordem de travas da carga = a do registro, que trava eventos → execucoes (o INSERT do evento,
-- depois o gatilho abaixo e a FK leem a execução). Por isso eventos vem PRIMEIRO, antes de
-- qualquer comando em execucoes: na ordem inversa, carga e registro esperam um pelo outro e o
-- Postgres derruba um dos dois (DeadlockDetected). SHARE ROW EXCLUSIVE é o que o CREATE OR
-- REPLACE TRIGGER pegaria de qualquer forma; leituras (SELECT) seguem durante a carga.
LOCK TABLE auditoria.eventos IN SHARE ROW EXCLUSIVE MODE;

-- Mudanças de esquema em banco antigo, só quando faltam (consulta ao catálogo antes): o
-- ALTER TABLE pega ACCESS EXCLUSIVE, que pararia até a leitura da trilha a cada carga.
-- * total_casos: banco criado antes da coluna; acrescenta sem mexer nas execuções gravadas.
-- * execucoes_status_valido: só os valores que o registro conhece (srag_agente.auditoria.
--   registro, STATUS_FINAIS; um teste amarra as duas listas).
-- * execucoes_fim_coerente: encerrada, sempre com fim; aberta, sem fim. Para o auditor é
--   redundante (não grava `fim` no INSERT e o gatilho impõe o fim no UPDATE); protege contra
--   o dono da tabela (ETL), que escapa dos dois.
-- Banco antigo pode ter linha fora da regra: a regra nasce NOT VALID (vale para o que se grava
-- daqui em diante, sem reescrever a trilha nem travar a carga). Enquanto não validada, toda
-- carga tenta validar (SHARE UPDATE EXCLUSIVE: não para leitura nem gravação do auditor), e
-- passa a valer para o histórico assim que ele estiver limpo.
DO $$
DECLARE
  regra record;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'auditoria.execucoes'::regclass
                 AND attname = 'total_casos' AND NOT attisdropped) THEN
    ALTER TABLE auditoria.execucoes ADD COLUMN total_casos int;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'auditoria.execucoes'::regclass
                 AND conname = 'execucoes_status_valido') THEN
    ALTER TABLE auditoria.execucoes ADD CONSTRAINT execucoes_status_valido
      CHECK (status IN ('em_andamento', 'ok', 'parcial', 'revisao_humana', 'erro')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'auditoria.execucoes'::regclass
                 AND conname = 'execucoes_fim_coerente') THEN
    ALTER TABLE auditoria.execucoes ADD CONSTRAINT execucoes_fim_coerente
      CHECK ((status = 'em_andamento') = (fim IS NULL)) NOT VALID;
  END IF;
  FOR regra IN SELECT conname FROM pg_constraint
               WHERE conrelid = 'auditoria.execucoes'::regclass AND NOT convalidated
                 AND conname IN ('execucoes_status_valido', 'execucoes_fim_coerente')
               ORDER BY conname LOOP
    BEGIN
      EXECUTE format('ALTER TABLE auditoria.execucoes VALIDATE CONSTRAINT %I', regra.conname);
    EXCEPTION WHEN check_violation THEN
      RAISE NOTICE 'auditoria.execucoes tem linha legada fora de %; a regra vale só para linhas novas',
        regra.conname;
    END;
  END LOOP;
END $$;

-- Execução encerrada não se reescreve: o UPDATE só vale enquanto o status é 'em_andamento', e
-- o único UPDATE possível é o encerramento (status final; fim = relógio do servidor, nunca o
-- que veio no comando). O auditor não é dono da tabela, então não desliga estes gatilhos.
-- search_path fixo: tabela temporária do invocador não se passa pela tabela real.
CREATE OR REPLACE FUNCTION auditoria.execucao_encerrada_nao_muda() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF OLD.status <> 'em_andamento' THEN
    RAISE EXCEPTION 'execução % já encerrada com status %', OLD.id_execucao, OLD.status
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.status = 'em_andamento' THEN
    RAISE EXCEPTION 'encerramento da execução % precisa de status final', OLD.id_execucao
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  -- clock_timestamp(): a hora do encerramento. now() seria o início da transação (igual ao
  -- início da execução, se as duas coisas acontecerem na mesma transação).
  NEW.fim := clock_timestamp();
  RETURN NEW;
END $$;

-- CREATE OR REPLACE TRIGGER (PG 14+) em vez de DROP + CREATE: pega SHARE ROW EXCLUSIVE (para
-- as gravações até o fim da carga) e não ACCESS EXCLUSIVE, então a leitura da auditoria segue
-- durante o transformar.
CREATE OR REPLACE TRIGGER execucao_encerrada_nao_muda
  BEFORE UPDATE ON auditoria.execucoes
  FOR EACH ROW EXECUTE FUNCTION auditoria.execucao_encerrada_nao_muda();

-- Evento só entra em execução aberta. FOR SHARE espera um encerramento em curso (que segura a
-- linha) e então enxerga o status já confirmado: sem isso, um evento simultâneo ao encerramento
-- leria o status antigo e entraria depois do fim. Execução inexistente: a FK recusa.
CREATE OR REPLACE FUNCTION auditoria.evento_so_em_execucao_aberta() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  situacao text;
BEGIN
  SELECT status INTO situacao FROM auditoria.execucoes
    WHERE id_execucao = NEW.id_execucao FOR SHARE;
  IF FOUND AND situacao <> 'em_andamento' THEN
    RAISE EXCEPTION 'execução % já encerrada com status %: a trilha não ganha eventos',
      NEW.id_execucao, situacao USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER evento_so_em_execucao_aberta
  BEFORE INSERT ON auditoria.eventos
  FOR EACH ROW EXECUTE FUNCTION auditoria.evento_so_em_execucao_aberta();

-- Permissões, do zero a cada execução: limpa concessão de versão anterior antes de conceder
-- (REVOKE na tabela também tira as concessões por coluna).
-- Agente e público: nada. Auditor: lê e acrescenta, e o INSERT é por coluna: a execução nasce
-- 'em_andamento', sem fim nem consumo, com início do servidor; o evento nasce com id e momento
-- do servidor. Assim ninguém grava uma execução já "ok" nem um horário inventado.
-- Na execução, o UPDATE é só das colunas de encerramento (spec §5 dá INSERT/SELECT; o
-- encerramento precisa gravar status e consumo). A identidade da execução (modelo, provedor,
-- código, prompts, parâmetros, início) fica imutável para o auditor.
REVOKE ALL ON auditoria.execucoes, auditoria.eventos FROM PUBLIC, srag_agente, srag_auditor;
REVOKE ALL ON SEQUENCE auditoria.eventos_id_seq FROM PUBLIC, srag_agente, srag_auditor;
GRANT SELECT ON auditoria.execucoes, auditoria.eventos TO srag_auditor;
GRANT INSERT (id_execucao, provedor, modelo, parametros, versao_codigo, hash_prompts)
  ON auditoria.execucoes TO srag_auditor;
GRANT INSERT (id_execucao, sequencia, no, tipo, ferramenta, argumentos, motivo, resultado,
              duracao_ms, tokens_entrada, tokens_saida, erro)
  ON auditoria.eventos TO srag_auditor;
GRANT UPDATE (fim, status, data_referencia, total_casos, tokens_entrada, tokens_saida, custo_usd)
  ON auditoria.execucoes TO srag_auditor;
GRANT USAGE ON SEQUENCE auditoria.eventos_id_seq TO srag_auditor;
