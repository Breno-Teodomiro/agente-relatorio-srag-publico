"""Prova pelo efeito que cada papel só alcança o que deve: tenta e confere o erro do banco."""

import psycopg
import pytest
from psycopg import errors
from pydantic import SecretStr

from srag_agente.banco import conectar
from srag_agente.config import USUARIOS
from srag_agente.dados.bootstrap import _comando_papel, preparar_banco
from srag_agente.dados.transformar import transformar
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


def test_tabela_nova_na_auditoria_nasce_sem_privilegio_para_o_auditor(
    conn_auditor, tabelas_de_teste
):
    """Gravar e ler a auditoria vale só para as tabelas concedidas por coluna em 04_auditoria.sql
    (testes abaixo); um INSERT por tabela inteira vindo do default seria permissão a mais."""
    with pytest.raises(errors.InsufficientPrivilege):
        conn_auditor.execute("INSERT INTO auditoria._teste_permissao (evento) VALUES ('x')")


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


# --- Camadas reais: gold e auditoria ---------------------------------------------------------
# As travas de sessão do agente (somente leitura, timeout) são só padrão e a própria sessão as
# desliga. Por isso todo teste de escrita desliga o somente leitura antes: quem barra é o GRANT.

TABELAS_FORA_DO_ALCANCE_DO_AGENTE = (
    "raw.srag", "silver.casos", "silver.qualidade", "silver.limites",
    "auditoria.execucoes", "auditoria.eventos",
)  # fmt: skip
VIEWS_GOLD = ("referencia", "casos_diarios", "indicadores", "janelas_aumento", "distribuicao")


def _tenta(cfg, papel: str, comando: str, parametros: tuple | None = None) -> None:
    with conectar(cfg, papel, autocommit=True) as conn:
        _liberar_escrita(conn)
        conn.execute(comando, parametros)


@pytest.mark.parametrize("tabela", TABELAS_FORA_DO_ALCANCE_DO_AGENTE)
def test_agente_nao_le_raw_silver_nem_auditoria_reais(cfg_teste, banco_carregado, tabela):
    with pytest.raises(errors.InsufficientPrivilege):
        _tenta(cfg_teste, "agente", f"SELECT 1 FROM {tabela} LIMIT 1")  # nome da lista fixa


@pytest.mark.parametrize("relacao", [*VIEWS_GOLD, "dicionario_trechos"])
def test_agente_le_toda_a_gold(conn_agente, relacao):
    conn_agente.execute(f"SELECT count(*) FROM gold.{relacao}")  # nome da lista fixa


def test_agente_le_a_gold_com_dados(conn_agente):
    assert conn_agente.execute("SELECT count(*) FROM gold.indicadores").fetchone()[0] > 0


@pytest.mark.parametrize(
    "comando",
    [
        "INSERT INTO gold.dicionario_trechos (pagina, texto, embedding) "
        "VALUES (1, 'x', array_fill(0, ARRAY[384])::vector)",
        "UPDATE gold.dicionario_trechos SET texto = 'x'",
        "DELETE FROM gold.dicionario_trechos",
        "TRUNCATE gold.dicionario_trechos",
        "DROP VIEW gold.indicadores",
        "CREATE OR REPLACE VIEW gold.referencia AS SELECT 1 AS x",
        "ALTER VIEW gold.indicadores RENAME TO indicadores_velhos",
        "CREATE TABLE gold.invasao (x int)",
        "INSERT INTO auditoria.eventos (id_execucao, sequencia, no, tipo) "
        "VALUES (gen_random_uuid(), 1, 'x', 'llm')",
    ],
)
def test_agente_nao_altera_gold_nem_auditoria(cfg_teste, banco_carregado, comando):
    with pytest.raises(errors.InsufficientPrivilege):
        _tenta(cfg_teste, "agente", comando)


@pytest.mark.parametrize("tabela", ["gold.indicadores", "gold.referencia", "silver.casos"])
def test_auditor_nao_le_dados(cfg_teste, banco_carregado, tabela):
    with pytest.raises(errors.InsufficientPrivilege):
        _tenta(cfg_teste, "auditor", f"SELECT 1 FROM {tabela} LIMIT 1")  # nome da lista fixa


EXECUCAO = (
    "INSERT INTO auditoria.execucoes (id_execucao, provedor, modelo, parametros, versao_codigo, "
    "hash_prompts) VALUES (%s, 'teste', 'modelo-teste', '{}', 'abc123', 'h') "
)


@pytest.fixture
def execucao_de_teste(conn_auditor, conn_etl):
    """Execução e evento gravados pelo auditor, como fará o registro de auditoria.

    Só o dono (ETL) apaga, e só aqui na limpeza: o auditor não tem DELETE.
    """
    id_execucao = conn_auditor.execute("SELECT gen_random_uuid()").fetchone()[0]
    conn_auditor.execute(EXECUCAO, (id_execucao,))
    conn_auditor.execute(
        "INSERT INTO auditoria.eventos (id_execucao, sequencia, no, tipo, motivo) "
        "VALUES (%s, 1, 'orquestrador', 'ferramenta', 'conferir métricas')",
        (id_execucao,),
    )
    yield id_execucao
    conn_etl.rollback()
    conn_etl.execute("DELETE FROM auditoria.eventos WHERE id_execucao = %s", (id_execucao,))
    conn_etl.execute("DELETE FROM auditoria.execucoes WHERE id_execucao = %s", (id_execucao,))
    conn_etl.commit()


def test_auditor_grava_e_le_execucao_e_evento(conn_auditor, execucao_de_teste):
    assert conn_auditor.execute(
        "SELECT x.status, e.tipo, e.motivo FROM auditoria.execucoes AS x "
        "JOIN auditoria.eventos AS e USING (id_execucao) WHERE id_execucao = %s",
        (execucao_de_teste,),
    ).fetchone() == ("em_andamento", "ferramenta", "conferir métricas")


def test_auditor_encerra_a_execucao_uma_vez(conn_auditor, execucao_de_teste):
    conn_auditor.execute(
        "UPDATE auditoria.execucoes SET fim = now(), status = 'ok', data_referencia = %s, "
        "total_casos = 10, tokens_entrada = 5, tokens_saida = 7, custo_usd = 0.000123 "
        "WHERE id_execucao = %s",
        ("2025-06-25", execucao_de_teste),
    )
    assert conn_auditor.execute(
        "SELECT status, fim IS NOT NULL, total_casos, tokens_saida FROM auditoria.execucoes "
        "WHERE id_execucao = %s",
        (execucao_de_teste,),
    ).fetchone() == ("ok", True, 10, 7)
    # Encerrada, a execução não se reescreve (nem para "corrigir" o status).
    with pytest.raises(errors.IntegrityConstraintViolation, match="encerrada"):
        conn_auditor.execute(
            "UPDATE auditoria.execucoes SET status = 'erro' WHERE id_execucao = %s",
            (execucao_de_teste,),
        )


@pytest.mark.parametrize(
    "comando",
    [
        # Identidade da execução: quem rodou, com que modelo, código e prompts.
        "UPDATE auditoria.execucoes SET modelo = 'outro' WHERE id_execucao = %s",
        "UPDATE auditoria.execucoes SET provedor = 'outro' WHERE id_execucao = %s",
        "UPDATE auditoria.execucoes SET hash_prompts = 'outro' WHERE id_execucao = %s",
        "UPDATE auditoria.execucoes SET versao_codigo = 'outro' WHERE id_execucao = %s",
        "UPDATE auditoria.execucoes SET parametros = '{}' WHERE id_execucao = %s",
        "UPDATE auditoria.execucoes SET inicio = now() WHERE id_execucao = %s",
        "UPDATE auditoria.execucoes SET id_execucao = gen_random_uuid() WHERE id_execucao = %s",
        "DELETE FROM auditoria.execucoes WHERE id_execucao = %s",
        # Eventos: só se acrescentam.
        "UPDATE auditoria.eventos SET motivo = 'adulterado' WHERE id_execucao = %s",
        "UPDATE auditoria.eventos SET erro = 'adulterado' WHERE id_execucao = %s",
        "DELETE FROM auditoria.eventos WHERE id_execucao = %s",
        "TRUNCATE auditoria.eventos, auditoria.execucoes",
        "ALTER TABLE auditoria.execucoes DISABLE TRIGGER USER",
        "SELECT setval('auditoria.eventos_id_seq', 1)",
    ],
)
def test_auditor_nao_reescreve_a_trilha(cfg_teste, execucao_de_teste, comando):
    with pytest.raises(errors.InsufficientPrivilege):
        _tenta(cfg_teste, "auditor", comando, (execucao_de_teste,) if "%s" in comando else None)


def test_transformar_tira_concessao_antiga_da_auditoria(cfg_teste, conn_etl, execucao_de_teste):
    # Banco criado por versão anterior, que dava UPDATE/DELETE ao auditor: o 04 limpa na volta.
    conn_etl.execute(
        "GRANT UPDATE, DELETE, TRUNCATE ON auditoria.execucoes, auditoria.eventos TO srag_auditor"
    )
    conn_etl.commit()
    _tenta(cfg_teste, "auditor", "UPDATE auditoria.eventos SET motivo = motivo WHERE false")
    transformar(conn_etl, ("04_auditoria.sql",))
    conn_etl.commit()
    for comando in (
        "UPDATE auditoria.eventos SET motivo = 'adulterado' WHERE id_execucao = %s",
        "DELETE FROM auditoria.execucoes WHERE id_execucao = %s",
        "UPDATE auditoria.execucoes SET modelo = 'outro' WHERE id_execucao = %s",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            _tenta(cfg_teste, "auditor", comando, (execucao_de_teste,))


def test_auditoria_de_versao_anterior_ganha_a_coluna_nova(conn_etl, conn_auditor):
    # Banco com auditoria.execucoes de antes de `total_casos`: o 04 acrescenta e concede.
    coluna = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'auditoria' AND table_name = 'execucoes' "
        "AND column_name = 'total_casos'"
    )
    conn_etl.execute("ALTER TABLE auditoria.execucoes DROP COLUMN total_casos")
    conn_etl.commit()
    assert conn_etl.execute(coluna).fetchone() == (0,)
    transformar(conn_etl, ("04_auditoria.sql",))
    conn_etl.commit()
    assert conn_etl.execute(coluna).fetchone() == (1,)
    conn_auditor.execute("UPDATE auditoria.execucoes SET total_casos = 0 WHERE false")
