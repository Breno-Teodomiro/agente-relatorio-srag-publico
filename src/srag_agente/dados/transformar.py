"""Executa, em ordem, os SQL versionados que constroem silver, gold e auditoria."""

import psycopg

from srag_agente.dados.bootstrap import ler_sql

ARQUIVOS_TRANSFORMACAO = ("01_silver.sql", "02_qualidade.sql", "03_gold.sql", "04_auditoria.sql")

# Chave da trava consultiva (pg_advisory_xact_lock): só um `transformar` por banco de cada vez.
# O número é arbitrário; só precisa ser o mesmo em todo processo que chama `transformar`.
TRAVA_TRANSFORMACAO = 7_350_301


def transformar(
    conn: psycopg.Connection, arquivos: tuple[str, ...] = ARQUIVOS_TRANSFORMACAO
) -> None:
    """Reconstrói as camadas do zero, tudo ou nada.

    Os arquivos rodam num único `conn.transaction()`: se um falhar, as camadas anteriores
    continuam como estavam, com a conexão em autocommit ou não. Se o chamador já tiver uma
    transação aberta, o bloco vira savepoint e a confirmação final fica com ele.
    A trava consultiva enfileira execuções simultâneas em vez de deixá-las colidir.
    """
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (TRAVA_TRANSFORMACAO,))
        for nome in arquivos:
            conn.execute(ler_sql(nome))
