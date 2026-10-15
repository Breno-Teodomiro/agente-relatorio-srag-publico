import pytest

from srag_agente.config import Config

SENHAS = {
    "PG_SENHA_ADMIN": "adm 1",
    "PG_SENHA_ETL": "etl 1",
    "PG_SENHA_AGENTE": "agente 1",
    "PG_SENHA_AUDITOR": "auditor 1",
}


@pytest.fixture
def cfg(monkeypatch) -> Config:
    for chave, valor in SENHAS.items():
        monkeypatch.setenv(chave, valor)
    return Config(_env_file=None)


def test_dsn_usa_o_usuario_do_papel(cfg):
    dsn = cfg.dsn("agente")
    assert "user=srag_agente" in dsn
    assert "port=5435" in dsn
    assert "dbname=srag" in dsn
    assert "agente 1" in dsn


def test_dsn_aceita_outro_banco(cfg):
    assert "dbname=srag_teste" in cfg.dsn("etl", banco="srag_teste")


def test_admin_usa_postgres(cfg):
    assert "user=postgres" in cfg.dsn("admin")


def test_senha_nao_vaza_no_repr(cfg):
    assert "agente 1" not in repr(cfg)


def test_padroes_do_llm(cfg):
    assert cfg.llm_provider == "openrouter"
    assert cfg.llm_modelo_openrouter == "openai/gpt-4.1-mini"
    assert cfg.max_chamadas_ferramenta == 12
