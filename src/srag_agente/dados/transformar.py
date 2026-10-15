"""Executa, em ordem, os SQL versionados que constroem silver, gold e auditoria."""

import psycopg

from srag_agente.dados.bootstrap import ler_sql

ARQUIVOS_TRANSFORMACAO = ("01_silver.sql", "02_qualidade.sql", "03_gold.sql", "04_auditoria.sql")

# Arquivo que recria tabelas com DROP ... CASCADE → arquivo que recria o que o CASCADE derruba.
# O 01 apaga silver.casos e, junto, todas as views da gold; só o 03 as traz de volta.
RECRIA_DEPENDENTES = {"01_silver.sql": "03_gold.sql"}

# Chave da trava consultiva (pg_advisory_xact_lock): só um `transformar` por banco de cada vez.
# O número é arbitrário; só precisa ser o mesmo em todo processo que chama `transformar`.
TRAVA_TRANSFORMACAO = 7_350_301


def _conferir_dependentes(arquivos: tuple[str, ...]) -> None:
    """Recusa, antes de tocar no banco, uma lista que derrubaria a gold sem recriá-la.

    Subconjunto é permitido (ex.: só o 04 para reaplicar permissões da auditoria); o que não
    pode é rodar o 01 sem o 03 depois dele: a transação confirmaria uma gold vazia de views.
    """
    for arquivo, dependente in RECRIA_DEPENDENTES.items():
        if arquivo in arquivos and dependente not in arquivos[arquivos.index(arquivo) + 1 :]:
            raise ValueError(
                f"{arquivo} apaga as views da gold (DROP ... CASCADE); "
                f"inclua {dependente} depois dele na mesma chamada."
            )


def transformar(
    conn: psycopg.Connection, arquivos: tuple[str, ...] = ARQUIVOS_TRANSFORMACAO
) -> None:
    """Reconstrói as camadas do zero, tudo ou nada.

    Os arquivos rodam num único `conn.transaction()`: se um falhar, as camadas anteriores
    continuam como estavam, com a conexão em autocommit ou não. Se o chamador já tiver uma
    transação aberta, o bloco vira savepoint e a confirmação final fica com ele.
    A trava consultiva enfileira execuções simultâneas em vez de deixá-las colidir.
    """
    _conferir_dependentes(arquivos)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (TRAVA_TRANSFORMACAO,))
        for nome in arquivos:
            conn.execute(ler_sql(nome))
