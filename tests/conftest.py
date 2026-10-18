"""Fixtures compartilhadas. Os testes de integração usam o banco `srag_teste` do Compose."""

import os

import psycopg
import pytest
from pydantic import ValidationError

from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.dados.bootstrap import preparar_banco
from srag_agente.dados.carga_raw import carregar_raw
from srag_agente.dados.transformar import transformar
from tests.dados.amostra import escrever_csv, gerar_linhas

BANCO_TESTE = "srag_teste"
AVISO_SEM_BANCO = "copie .env.example para .env e rode `make subir`"
# Sem rota até o Postgres a conexão esperaria o timeout do sistema (~2 min): a sonda desiste antes.
SEGUNDOS_SONDA = 2
_SENHA_AUSENTE = "sem-env"


@pytest.fixture(scope="session")
def cfg_teste() -> Config:
    """Config do banco de teste. Sem `.env` (clone limpo) usa senhas de mentira: os testes que não
    tocam o banco seguem rodando e `banco_carregado` pula os de integração."""
    try:
        return Config(pg_banco=BANCO_TESTE)
    except ValidationError:
        papeis = ("admin", "etl", "agente", "auditor")
        senhas = {f"pg_senha_{papel}": _SENHA_AUSENTE for papel in papeis}
        return Config(_env_file=None, pg_banco=BANCO_TESTE, **senhas)


def _exigir_banco() -> bool:
    return os.environ.get("EXIGIR_BANCO") == "1"


@pytest.fixture(scope="session")
def banco_carregado(cfg_teste, tmp_path_factory) -> list[dict[str, str]]:
    """Prepara `srag_teste`, carrega a amostra e roda as transformações uma vez por sessão."""
    try:
        # Sonda única e curta: a falha vira skip da sessão inteira (a fixture roda uma vez).
        with psycopg.connect(cfg_teste.dsn("admin", "postgres"), connect_timeout=SEGUNDOS_SONDA):
            pass
        preparar_banco(cfg_teste, BANCO_TESTE)
    except psycopg.OperationalError as erro:
        if _exigir_banco():
            raise
        pytest.skip(f"Postgres indisponível ({AVISO_SEM_BANCO}): {erro}")
    linhas = gerar_linhas()
    csv = escrever_csv(tmp_path_factory.mktemp("dados") / "amostra.csv", linhas)
    with conectar(cfg_teste, "etl") as conn:
        carregar_raw(conn, csv)
        transformar(conn)
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
