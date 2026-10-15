"""Prova pelo efeito que cada papel só alcança o que deve: tenta e confere o erro do banco."""

import psycopg
import pytest
from psycopg import errors
from pydantic import SecretStr

from srag_agente.banco import conectar
from srag_agente.config import USUARIOS
from srag_agente.dados.bootstrap import _comando_papel, preparar_banco
from tests.conftest import BANCO_TESTE

pytestmark = pytest.mark.integracao

ESQUEMAS = ("raw", "silver", "gold", "auditoria")


@pytest.fixture
def tabelas_de_teste(conn_etl):
    """Tabelas criadas pelo ETL em gold e auditoria, como farão as transformações."""
    _apagar_tabelas_de_teste(conn_etl)  # sobra de uma execução interrompida
    conn_etl.execute("CREATE TABLE gold._teste_permissao (valor int)")
    conn_etl.execute("INSERT INTO gold._teste_permissao VALUES (42)")
    conn_etl.execute(
        "CREATE TABLE auditoria._teste_permissao (id bigserial PRIMARY KEY, evento text)"
    )
    conn_etl.commit()
    yield
    conn_etl.rollback()
    _apagar_tabelas_de_teste(conn_etl)


def _apagar_tabelas_de_teste(conn: psycopg.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS gold._teste_permissao")
    conn.execute("DROP TABLE IF EXISTS auditoria._teste_permissao")
    conn.commit()


def _liberar_escrita(conn: psycopg.Connection) -> None:
    """O que um invasor tentaria primeiro: desligar o somente leitura da própria sessão."""
    conn.execute("SET default_transaction_read_only = off")


# --- ETL -------------------------------------------------------------------------------------


def test_etl_e_dono_dos_quatro_esquemas(conn_etl):
    donos = dict(
        conn_etl.execute(
            "SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = ANY(%s)",
            (list(ESQUEMAS),),
        ).fetchall()
    )
    assert donos == dict.fromkeys(ESQUEMAS, "srag_etl")


def test_nenhum_papel_da_aplicacao_tem_poder_de_administrador(conn_etl):
    poderes = conn_etl.execute(
        "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles "
        "WHERE rolname IN ('srag_etl', 'srag_agente', 'srag_auditor') ORDER BY rolname"
    ).fetchall()
    assert [nome for nome, *_ in poderes] == ["srag_agente", "srag_auditor", "srag_etl"]
    assert all(not any(flags) for _, *flags in poderes)


# --- Agente ----------------------------------------------------------------------------------


def test_agente_le_tabela_que_o_etl_cria_na_gold(conn_agente, tabelas_de_teste):
    assert conn_agente.execute("SELECT valor FROM gold._teste_permissao").fetchone() == (42,)


@pytest.mark.parametrize("consulta", ["SELECT * FROM raw.srag", "SELECT * FROM silver.qualquer"])
def test_agente_nao_le_raw_nem_silver(conn_agente, consulta):
    with pytest.raises(errors.InsufficientPrivilege):
        conn_agente.execute(consulta)


def test_agente_nao_le_auditoria(conn_agente, tabelas_de_teste):
    with pytest.raises(errors.InsufficientPrivilege):
        conn_agente.execute("SELECT * FROM auditoria._teste_permissao")


def test_sessao_do_agente_e_somente_leitura(conn_agente):
    assert conn_agente.execute("SHOW transaction_read_only").fetchone() == ("on",)
    with pytest.raises(errors.ReadOnlySqlTransaction):
        conn_agente.execute("CREATE TEMP TABLE t (x int)")


@pytest.mark.parametrize(
    "comando",
    [
        "INSERT INTO gold._teste_permissao VALUES (1)",
        "UPDATE gold._teste_permissao SET valor = 0",
        "DELETE FROM gold._teste_permissao",
        "CREATE TABLE gold.invasor (x int)",
        "CREATE TABLE public.invasor (x int)",
        "DROP TABLE gold._teste_permissao",
    ],
)
def test_agente_nao_grava_nem_desligando_o_somente_leitura(conn_agente, tabelas_de_teste, comando):
    _liberar_escrita(conn_agente)
    with pytest.raises(errors.InsufficientPrivilege):
        conn_agente.execute(comando)


def test_consulta_longa_do_agente_e_cancelada_em_5s(conn_agente):
    assert conn_agente.execute("SHOW statement_timeout").fetchone() == ("5s",)
    with pytest.raises(errors.QueryCanceled):
        conn_agente.execute("SELECT pg_sleep(6)")


# --- Auditor ---------------------------------------------------------------------------------


def test_auditor_grava_e_le_a_auditoria(conn_auditor, tabelas_de_teste):
    (id_evento,) = conn_auditor.execute(
        "INSERT INTO auditoria._teste_permissao (evento) VALUES ('inicio') RETURNING id"
    ).fetchone()
    assert conn_auditor.execute(
        "SELECT evento FROM auditoria._teste_permissao WHERE id = %s", (id_evento,)
    ).fetchone() == ("inicio",)


@pytest.mark.parametrize(
    "comando",
    [
        "UPDATE auditoria._teste_permissao SET evento = 'adulterado'",
        "DELETE FROM auditoria._teste_permissao",
        "SELECT * FROM raw.srag",
        "SELECT * FROM gold._teste_permissao",
        "CREATE TABLE auditoria.invasor (x int)",
    ],
)
def test_auditor_nao_sai_da_auditoria(conn_auditor, tabelas_de_teste, comando):
    with pytest.raises(errors.InsufficientPrivilege):
        conn_auditor.execute(comando)


# --- Bootstrap -------------------------------------------------------------------------------


def test_comando_de_papel_nao_leva_a_senha_em_texto_puro(cfg_teste, banco_carregado):
    senha = cfg_teste._senha("agente")
    with conectar(cfg_teste, "admin", banco="postgres", autocommit=True) as conn:
        comando = _comando_papel(conn, USUARIOS["agente"], senha, existe=True).as_string(conn)
    assert senha not in comando
    assert "SCRAM-SHA-256$" in comando


def test_bootstrap_de_novo_nao_perde_dados_nem_permissoes(cfg_teste, conn_etl, banco_carregado):
    preparar_banco(cfg_teste, BANCO_TESTE)
    assert conn_etl.execute("SELECT count(*) FROM raw.srag").fetchone()[0] == len(banco_carregado)
    with (
        conectar(cfg_teste, "agente", autocommit=True) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute("SELECT 1 FROM raw.srag LIMIT 1")


def test_papeis_entram_com_a_senha_do_env_depois_do_bootstrap(cfg_teste, banco_carregado):
    for papel in ("etl", "agente", "auditor"):
        with conectar(cfg_teste, papel) as conn:
            usuario = conn.execute("SELECT current_user").fetchone()[0]
        assert usuario == USUARIOS[papel]


def test_senha_errada_nao_entra(cfg_teste, banco_carregado):
    errada = cfg_teste.model_copy(update={"pg_senha_agente": SecretStr("errada")})
    with pytest.raises(psycopg.OperationalError):
        conectar(errada, "agente")


def test_esquemas_sensiveis_nao_estao_abertos_ao_publico(conn_etl):
    abertos = conn_etl.execute(
        "SELECT s FROM unnest(%s::text[]) AS s WHERE has_schema_privilege('public', s, 'USAGE')",
        (list(ESQUEMAS),),
    ).fetchall()
    assert abertos == []
