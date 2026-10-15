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
-- Banco criado antes da coluna existir: acrescenta sem mexer nas execuções já gravadas.
ALTER TABLE auditoria.execucoes ADD COLUMN IF NOT EXISTS total_casos int;

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

-- Execução encerrada não se reescreve: o UPDATE só vale enquanto o status é 'em_andamento'.
-- O auditor não é dono da tabela, então não consegue desligar este gatilho.
CREATE OR REPLACE FUNCTION auditoria.execucao_encerrada_nao_muda() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'em_andamento' THEN
    RAISE EXCEPTION 'execução % já encerrada com status %', OLD.id_execucao, OLD.status
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS execucao_encerrada_nao_muda ON auditoria.execucoes;
CREATE TRIGGER execucao_encerrada_nao_muda
  BEFORE UPDATE ON auditoria.execucoes
  FOR EACH ROW EXECUTE FUNCTION auditoria.execucao_encerrada_nao_muda();

-- Permissões, do zero a cada execução: limpa concessão de versão anterior antes de conceder.
-- Agente e público: nada. Auditor: lê e acrescenta; eventos só se acrescentam.
-- Na execução, o UPDATE é só das colunas de encerramento (spec §5 dá INSERT/SELECT; o
-- encerramento precisa gravar fim, status e consumo). A identidade da execução (modelo,
-- provedor, código, prompts, parâmetros, início) fica imutável para o auditor.
REVOKE ALL ON auditoria.execucoes, auditoria.eventos FROM PUBLIC, srag_agente, srag_auditor;
REVOKE ALL ON SEQUENCE auditoria.eventos_id_seq FROM PUBLIC, srag_agente, srag_auditor;
GRANT SELECT, INSERT ON auditoria.execucoes, auditoria.eventos TO srag_auditor;
GRANT UPDATE (fim, status, data_referencia, total_casos, tokens_entrada, tokens_saida, custo_usd)
  ON auditoria.execucoes TO srag_auditor;
GRANT USAGE ON SEQUENCE auditoria.eventos_id_seq TO srag_auditor;
