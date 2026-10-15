"""Fixtures compartilhadas. Os testes de integração usam o banco `srag_teste` do Compose."""

import os

import psycopg
import pytest

from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.dados.bootstrap import preparar_banco
from srag_agente.dados.carga_raw import carregar_raw
from tests.dados.amostra import escrever_csv, gerar_linhas

BANCO_TESTE = "srag_teste"


@pytest.fixture(scope="session")
def cfg_teste() -> Config:
    return Config(pg_banco=BANCO_TESTE)


@pytest.fixture(scope="session")
def banco_carregado(cfg_teste, tmp_path_factory) -> list[dict[str, str]]:
    """Prepara `srag_teste`, carrega a amostra e roda as transformações uma vez por sessão."""
    try:
        preparar_banco(cfg_teste, BANCO_TESTE)
    except psycopg.OperationalError as erro:
        if os.environ.get("EXIGIR_BANCO") == "1":
            raise
        pytest.skip(f"Postgres indisponível (rode `make subir`): {erro}")
    linhas = gerar_linhas()
    csv = escrever_csv(tmp_path_factory.mktemp("dados") / "amostra.csv", linhas)
    with conectar(cfg_teste, "etl") as conn:
        carregar_raw(conn, csv)
    return linhas


@pytest.fixture
def conn_etl(cfg_teste, banco_carregado):
    with conectar(cfg_teste, "etl") as conn:
        yield conn


@pytest.fixture
def conn_agente(cfg_teste, banco_carregado):
    with conectar(cfg_teste, "agente", autocommit=True) as conn:
        yield conn


@pytest.fixture
def conn_auditor(cfg_teste, banco_carregado):
    with conectar(cfg_teste, "auditor", autocommit=True) as conn:
        yield conn
