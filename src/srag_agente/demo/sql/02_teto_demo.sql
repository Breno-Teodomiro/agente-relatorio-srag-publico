-- Teto diário da demonstração (só no banco da demo; o 04_auditoria.sql local não muda).
-- Limites como dado versionado: a semente abaixo é a fonte. Mudar o limite = mudar este arquivo,
-- commitar e carregar de novo (o histórico do arquivo é a auditoria da regra). Os números são
-- política, não medida.
CREATE TABLE IF NOT EXISTS auditoria.limites_demo (
  id integer PRIMARY KEY CHECK (id = 1),
  relatorios_dia integer NOT NULL CHECK (relatorios_dia > 0),
  buscas_dia integer NOT NULL CHECK (buscas_dia > 0),
  custo_usd_dia numeric(10, 4) NOT NULL CHECK (custo_usd_dia > 0),
  fuso text NOT NULL
);

INSERT INTO auditoria.limites_demo (id, relatorios_dia, buscas_dia, custo_usd_dia, fuso)
VALUES (1, 10, 40, 1.00, 'America/Sao_Paulo')
ON CONFLICT (id) DO UPDATE SET
  relatorios_dia = EXCLUDED.relatorios_dia, buscas_dia = EXCLUDED.buscas_dia,
  custo_usd_dia = EXCLUDED.custo_usd_dia, fuso = EXCLUDED.fuso;

-- Barreira do banco: a execução N+1 do dia não nasce, com qualquer status contando (o crédito
-- pode ter sido gasto mesmo em erro). Dia = meia-noite a meia-noite no fuso da tabela, pelo
-- relógio do servidor.
-- * A trava consultiva de TRANSAÇÃO serializa a contagem entre conexões: com o limite a um
--   passo, duas sessões ao mesmo tempo não passam as duas. Ela fica presa até o COMMIT.
-- * Só em READ COMMITTED: cada comando da função enxerga o que foi confirmado depois da trava.
--   Num retrato fixo (REPEATABLE READ ou SERIALIZABLE) a segunda sessão contaria a partir de um
--   retrato anterior à primeira e furaria o teto; por isso é recusado.
-- * Sem linha de limites, recusa (falha fechada). search_path fixo, como no 04_auditoria.sql.
-- * O auditor não é dono da tabela: não desliga o gatilho nem muda os limites.
-- * lock_timeout: quem segurar a trava (outra sessão presa) não pendura a tela; o INSERT desiste
--   em 5 s com erro de trava (55P03), sem gravar nada.
CREATE OR REPLACE FUNCTION auditoria.teto_diario_de_execucoes() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp SET lock_timeout = '5s' AS $$
DECLARE
  limite auditoria.limites_demo;
  desde timestamptz;
  feitas integer;
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'o teto diário da demonstração só aceita execução em READ COMMITTED'
      USING ERRCODE = 'SR429';
  END IF;
  PERFORM pg_advisory_xact_lock(7350302);  -- TRAVA_TETO (vizinha da trava da transformação)
  SELECT * INTO limite FROM auditoria.limites_demo WHERE id = 1;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'demonstração sem limites configurados' USING ERRCODE = 'SR429';
  END IF;
  desde := date_trunc('day', now() AT TIME ZONE limite.fuso) AT TIME ZONE limite.fuso;
  SELECT count(*) INTO feitas FROM auditoria.execucoes WHERE inicio >= desde;
  IF feitas >= limite.relatorios_dia THEN
    RAISE EXCEPTION 'limite diário de % relatórios da demonstração atingido', limite.relatorios_dia
      USING ERRCODE = 'SR429';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER teto_diario_de_execucoes
  BEFORE INSERT ON auditoria.execucoes
  FOR EACH ROW EXECUTE FUNCTION auditoria.teto_diario_de_execucoes();

REVOKE ALL ON auditoria.limites_demo FROM PUBLIC, srag_agente, srag_auditor;
GRANT SELECT ON auditoria.limites_demo TO srag_auditor;

-- Volume da trilha, só no banco da demo: com a credencial do auditor vazada, uma execução aberta
-- aceitaria eventos sem fim e lotaria o armazenamento do plano gratuito. Teto estrutural:
-- execuções por dia (gatilho acima) x eventos por execução (sequencia, com o UNIQUE de
-- 04_auditoria.sql) x bytes por evento. Folga sobre o código, não sobre a medida de um dia:
-- * sequencia 1..100: o registro numera de 1 em 1; o teto de ferramentas por execução é 12;
-- * argumentos/resultado até 8192 bytes: o registro corta o resumo em 2000 caracteres (até
--   ~4000 bytes em português) e preserva só campos curtos de primeiro nível;
-- * motivo/erro até 8192 bytes (cortados em 2000 caracteres pelo registro);
-- * parametros até 4096 bytes (a pergunta complementar tem no máximo 500 caracteres);
-- * textos de identificação (nó, tipo, ferramenta, modelo, provedor...) até 256 bytes.
-- Medida da trilha real (comando, não número): SELECT max(sequencia),
-- max(octet_length(argumentos::text)), max(octet_length(resultado::text)) FROM auditoria.eventos;
-- NOT VALID: vale para o que se grava daqui em diante sem reescrever a trilha; cada carga tenta
-- validar o histórico (mesmo padrão do 04_auditoria.sql).
DO $$
DECLARE
  regra record;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'auditoria.eventos'::regclass
                 AND conname = 'eventos_volume_demo') THEN
    ALTER TABLE auditoria.eventos ADD CONSTRAINT eventos_volume_demo CHECK (
      sequencia BETWEEN 1 AND 100
      AND octet_length(argumentos::text) <= 8192 AND octet_length(resultado::text) <= 8192
      AND octet_length(motivo) <= 8192 AND octet_length(erro) <= 8192
      AND octet_length(no) <= 256 AND octet_length(tipo) <= 256
      AND octet_length(ferramenta) <= 256
    ) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'auditoria.execucoes'::regclass
                 AND conname = 'execucoes_volume_demo') THEN
    ALTER TABLE auditoria.execucoes ADD CONSTRAINT execucoes_volume_demo CHECK (
      octet_length(parametros::text) <= 4096
      AND octet_length(provedor) <= 256 AND octet_length(modelo) <= 256
      AND octet_length(versao_codigo) <= 256 AND octet_length(hash_prompts) <= 256
    ) NOT VALID;
  END IF;
  FOR regra IN SELECT conrelid::regclass AS tabela, conname FROM pg_constraint
               WHERE conname IN ('eventos_volume_demo', 'execucoes_volume_demo')
                 AND NOT convalidated ORDER BY conname LOOP
    BEGIN
      EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I', regra.tabela, regra.conname);
    EXCEPTION WHEN check_violation THEN
      RAISE NOTICE '% tem linha antiga fora de %; a regra vale só para linhas novas',
        regra.tabela, regra.conname;
    END;
  END LOOP;
END $$;
