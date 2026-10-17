"""Indexa o dicionário de dados em gold.dicionario_trechos (roda como ETL)."""

from pathlib import Path

import numpy as np
import psycopg
import pymupdf
from pgvector.psycopg import register_vector

from srag_agente.rag.embeddings import Embedder
from srag_agente.rag.trechos import dividir_em_trechos


def extrair_paginas(pdf: Path) -> list[str]:
    with pymupdf.open(pdf) as documento:
        return [pagina.get_text() for pagina in documento]


def campos_da_raw(conn: psycopg.Connection) -> set[str]:
    linhas = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'raw' AND table_name = 'srag'"
    ).fetchall()
    return {nome.upper() for (nome,) in linhas}


CAMPOS_DAS_METRICAS = frozenset({"EVOLUCAO", "UTI", "VACINA_COV", "CLASSI_FIN"})


def indexar_dicionario(
    conn: psycopg.Connection, pdf: Path, embedder: Embedder, campos_conhecidos: set[str]
) -> int:
    """Troca o índice inteiro numa transação. `conn` deve estar em autocommit (sem transação
    aberta), para que `conn.transaction()` seja a transação de fato e confirme ao sair.

    Os nomes de campo vêm da raw (o PDF não os distingue de palavras em maiúsculas). Sem eles
    o PDF viraria 1 trecho: por isso falha ANTES de apagar o índice que já existe.
    """
    if not conn.autocommit:
        raise ValueError("indexar_dicionario exige conexão em autocommit")
    if not campos_conhecidos:
        raise ValueError("sem campos conhecidos (raw.srag vazia ou ausente): índice preservado")
    trechos = dividir_em_trechos(extrair_paginas(pdf), campos_conhecidos)
    faltam = CAMPOS_DAS_METRICAS - {t.campo for t in trechos}
    if faltam:
        raise ValueError(f"campos das métricas sem trecho {sorted(faltam)}: índice preservado")
    vetores = embedder([t.texto for t in trechos])
    register_vector(conn)
    with conn.transaction():
        conn.execute("TRUNCATE gold.dicionario_trechos RESTART IDENTITY")
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO gold.dicionario_trechos (campo, pagina, texto, embedding) "
                "VALUES (%s, %s, %s, %s)",
                [
                    (t.campo, t.pagina, t.texto, np.array(v))
                    for t, v in zip(trechos, vetores, strict=True)
                ],
            )
    return len(trechos)
