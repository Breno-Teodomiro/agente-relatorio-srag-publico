import pytest

from tests.dados.amostra import COLUNAS

pytestmark = pytest.mark.integracao


def test_raw_preserva_todas_as_linhas(conn_etl, banco_carregado):
    total = conn_etl.execute("SELECT count(*) FROM raw.srag").fetchone()[0]
    assert total == len(banco_carregado)


def test_raw_tem_todas_as_colunas_como_texto(conn_etl):
    colunas = conn_etl.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'raw' AND table_name = 'srag' ORDER BY ordinal_position"
    ).fetchall()
    assert [nome for nome, _ in colunas] == [c.lower() for c in COLUNAS]
    assert {tipo for _, tipo in colunas} == {"text"}


def test_raw_guarda_o_valor_original_sem_tratar(conn_etl):
    valor = conn_etl.execute("SELECT dt_interna FROM raw.srag WHERE nu_notific = '1'").fetchone()[0]
    assert valor == "1695-01-17 02:32:37.742690304"
