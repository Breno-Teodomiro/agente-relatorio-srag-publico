"""Permissões do banco da demo pelo efeito: o mesmo `conferir_demo` que roda no banco remoto."""

import pytest

from srag_agente.banco import conectar
from srag_agente.demo import TABELAS_DEMO
from srag_agente.demo.carregar import carregar
from srag_agente.demo.conferir import conferir_demo

pytestmark = pytest.mark.integracao


def _falhas(cfg) -> dict[str, str]:
    return {f"{c.papel}: {c.tentativa}": c.obtido for c in conferir_demo(cfg) if not c.ok}


def test_conferir_demo_todo_verde(carga_demo):
    conferencias = conferir_demo(carga_demo.cfg)
    assert {f"{c.papel}: {c.tentativa}": c.obtido for c in conferencias if not c.ok} == {}
    assert len(conferencias) >= 20  # a tabela da §2.4 inteira, mais as consultas de catálogo


def test_papel_de_carga_nao_alcanca_nada_no_banco_da_demo(carga_demo):
    # Simulação: o srag_etl existe no cluster local (no provedor não existirá).
    with conectar(carga_demo.cfg, "admin", autocommit=True) as conn:
        esquemas = conn.execute(
            "SELECT s, has_schema_privilege('srag_etl', s, 'USAGE'), "
            "has_schema_privilege('srag_etl', s, 'CREATE') FROM unnest(%s::text[]) AS s",
            (["gold", "auditoria", "public"],),
        ).fetchall()
        tabelas = conn.execute(
            "SELECT t, has_table_privilege('srag_etl', 'gold.' || t, "
            "'SELECT, INSERT, UPDATE, DELETE, TRUNCATE') FROM unnest(%s::text[]) AS t",
            ([*TABELAS_DEMO, "pacote_demo"],),
        ).fetchall()
        temporaria = conn.execute(
            "SELECT has_database_privilege('srag_etl', current_database(), 'TEMPORARY')"
        ).fetchone()[0]
    assert [e for e in esquemas if e[0] != "public" and (e[1] or e[2])] == []
    assert [e for e in esquemas if e[0] == "public" and e[2]] == []  # nem cria em public
    assert [t for t in tabelas if t[1]] == []
    assert temporaria is False


@pytest.mark.parametrize(
    "afrouxar,falha",
    [
        ("GRANT INSERT ON gold.referencia TO srag_agente",
         "agente (somente leitura desligado): INSERT INTO gold.referencia"),
        ("GRANT TEMPORARY ON DATABASE {banco} TO srag_auditor", "auditor: CREATE TEMP TABLE"),
        ("GRANT CREATE ON SCHEMA public TO srag_agente",
         "agente (somente leitura desligado): CREATE TABLE public.t"),
        ("GRANT USAGE ON SCHEMA gold TO srag_auditor; GRANT SELECT ON gold.referencia TO "
         "srag_auditor", "auditor: SELECT em gold.referencia"),
        ("GRANT UPDATE ON auditoria.limites_demo TO srag_auditor",
         "auditor: UPDATE auditoria.limites_demo"),
        ("GRANT USAGE ON SCHEMA auditoria TO srag_agente; "
         "GRANT SELECT ON auditoria.execucoes TO srag_agente",
         "agente: SELECT em auditoria.execucoes"),
        ("ALTER TABLE auditoria.execucoes DISABLE TRIGGER teto_diario_de_execucoes",
         "catalogo: gatilho teto_diario_de_execucoes"),
        ("CREATE TABLE public.sobra (x int)", "catalogo: objetos fora de gold/auditoria"),
        ("CREATE SCHEMA silver", "catalogo: esquemas raw/silver"),
        ("CREATE EXTENSION pg_trgm", "catalogo: extensões fora de plpgsql/vector"),
        ("CREATE TABLE gold.casos_brutos (x int)", "catalogo: objetos de gold e auditoria"),
        ("GRANT INSERT (inicio) ON auditoria.execucoes TO srag_auditor",
         "auditor: INSERT com inicio em auditoria.execucoes"),
        ("DELETE FROM auditoria.limites_demo", "auditor: SELECT count(*) FROM "
         "auditoria.limites_demo"),
    ],
    ids=["agente_insert", "auditor_temp", "agente_public", "auditor_gold", "auditor_limites",
         "agente_auditoria", "gatilho_desligado", "objeto_em_public", "silver", "pg_trgm",
         "gold_a_mais",
         "auditor_inicio", "sem_limites"],
)  # fmt: skip
def test_conferir_pega_cada_afrouxamento(banco_descartavel, carga_demo, afrouxar, falha):
    # Prova que a conferência não é verde por construção: cada furo aparece no item certo.
    cfg = banco_descartavel
    carregar(cfg, carga_demo.pacote, alvo=cfg.pg_host, permitir_local=True)
    assert _falhas(cfg) == {}
    with conectar(cfg, "admin", autocommit=True) as conn:
        conn.execute(afrouxar.format(banco=cfg.pg_banco))
    assert falha in _falhas(cfg)


CONFIGS = "catalogo: configurações de srag_agente e srag_auditor"


@pytest.mark.parametrize(
    "papel,config",
    [("srag_auditor", "default_transaction_isolation = 'repeatable read'"),
     ("srag_agente", "statement_timeout = 0"),
     ("srag_agente", "default_transaction_read_only = off")],
)  # fmt: skip
def test_config_do_papel_deformada_e_acusada_e_a_recarga_repara(banco_descartavel, carga_demo,
                                                                papel, config):  # fmt: skip
    # O próprio papel consegue `ALTER ROLE <si> SET ...`. Aqui só neste banco (IN DATABASE),
    # para não mexer nas outras sessões que usam os mesmos papéis do cluster.
    cfg = banco_descartavel
    carregar(cfg, carga_demo.pacote, alvo=cfg.pg_host, permitir_local=True)
    usuario = "agente" if papel == "srag_agente" else "auditor"
    from srag_agente.banco import conectar as abrir

    with abrir(cfg, usuario, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = off")  # o que o invasor faz primeiro
        conn.execute(f"ALTER ROLE {papel} IN DATABASE {cfg.pg_banco} SET {config}")
    falhas = _falhas(cfg)
    assert CONFIGS in falhas and papel in falhas[CONFIGS]
    assert config.split(" = ")[1].strip("'") not in falhas[CONFIGS]  # só a chave, nunca o valor
    resumo = carregar(cfg, carga_demo.pacote, alvo=cfg.pg_host, permitir_local=True)
    assert resumo.ok and _falhas(cfg) == {}


def test_texto_do_gatilho_reflete_o_estado(banco_descartavel, carga_demo):
    cfg = banco_descartavel
    carregar(cfg, carga_demo.pacote, alvo=cfg.pg_host, permitir_local=True)
    chave = "catalogo: gatilho teto_diario_de_execucoes"

    def obtido():
        return {f"{c.papel}: {c.tentativa}": c.obtido for c in conferir_demo(cfg)}[chave]

    assert obtido() == "ativo"
    with conectar(cfg, "admin", autocommit=True) as conn:
        conn.execute("ALTER TABLE auditoria.execucoes DISABLE TRIGGER teto_diario_de_execucoes")
    assert obtido() == "desligado"
    with conectar(cfg, "admin", autocommit=True) as conn:
        conn.execute("DROP TRIGGER teto_diario_de_execucoes ON auditoria.execucoes")
    assert obtido() == "ausente"
