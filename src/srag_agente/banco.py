"""Abertura de conexões com o papel (usuário) certo para cada uso."""

import psycopg

from srag_agente.config import Config, Papel


def conectar(
    cfg: Config, papel: Papel, banco: str | None = None, autocommit: bool = False
) -> psycopg.Connection:
    return psycopg.connect(cfg.dsn(papel, banco), autocommit=autocommit)
