"""Busca semântica no dicionário de dados (somente leitura, usuário do agente)."""

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from pydantic import BaseModel

from srag_agente.rag.embeddings import Embedder

_SQL_BUSCA = (
    "SELECT campo, pagina, texto, embedding <=> %s AS distancia "
    "FROM gold.dicionario_trechos ORDER BY distancia LIMIT %s"
)


class TrechoDicionario(BaseModel):
    campo: str | None
    pagina: int
    texto: str
    distancia: float


class BuscaDicionario:
    def __init__(self, conn: psycopg.Connection, embedder: Embedder, k: int = 3) -> None:
        self._conn = conn
        self._embedder = embedder
        self._k = k
        register_vector(conn)

    def buscar(self, pergunta: str) -> list[TrechoDicionario]:
        vetor = np.array(self._embedder([pergunta])[0])
        linhas = self._conn.execute(_SQL_BUSCA, (vetor, self._k)).fetchall()
        return [
            TrechoDicionario(campo=campo, pagina=pagina, texto=texto, distancia=float(distancia))
            for campo, pagina, texto, distancia in linhas
        ]
