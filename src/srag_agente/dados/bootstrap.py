"""Cria banco, papéis (usuários) e schemas. Idempotente; é a única etapa que usa o admin."""

from importlib.resources import files

import psycopg
from psycopg import sql

from srag_agente.banco import conectar
from srag_agente.config import USUARIOS, Config

PAPEIS_DA_APLICACAO = ("etl", "agente", "auditor")


def ler_sql(nome: str) -> str:
    return (files("srag_agente.dados.sql") / nome).read_text(encoding="utf-8")


def _criar_banco_se_preciso(conn: psycopg.Connection, banco: str) -> None:
    existe = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (banco,)).fetchone()
    if not existe:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(banco)))


def _comando_papel(
    conn: psycopg.Connection | None,
    usuario: str,
    senha: str,
    existe: bool,
    em_hash: bool = True,
) -> sql.Composed:
    """Monta o CREATE/ALTER ROLE; por padrão com a senha já em hash SCRAM, calculado no cliente.

    Assim a senha em texto puro nunca trafega no comando, e não aparece em log do servidor,
    em `pg_stat_activity` nem em `pg_stat_statements`.
    `em_hash=False` manda a senha como texto: só para provedor que recusa hash (o banco da
    demonstração) e só sobre TLS; quem chama confere a conexão cifrada antes.
    """
    if em_hash:
        senha = conn.pgconn.encrypt_password(
            senha.encode("utf-8"), usuario.encode("utf-8"), b"scram-sha-256"
        ).decode("ascii")
    comando = (
        "ALTER ROLE {} WITH LOGIN PASSWORD {}" if existe else "CREATE ROLE {} LOGIN PASSWORD {}"
    )
    return sql.SQL(comando).format(sql.Identifier(usuario), sql.Literal(senha))


def _criar_papeis(conn: psycopg.Connection, cfg: Config) -> None:
    for papel in PAPEIS_DA_APLICACAO:
        usuario = USUARIOS[papel]
        existe = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (usuario,)).fetchone()
        conn.execute(_comando_papel(conn, usuario, cfg._senha(papel), existe=bool(existe)))


def preparar_banco(cfg: Config, banco: str) -> None:
    with conectar(cfg, "admin", banco="postgres", autocommit=True) as conn:
        _criar_banco_se_preciso(conn, banco)
        _criar_papeis(conn, cfg)
    with conectar(cfg, "admin", banco=banco, autocommit=True) as conn:
        conn.execute(ler_sql("00_estrutura.sql"))
