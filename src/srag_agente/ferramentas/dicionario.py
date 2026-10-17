"""Busca semântica no dicionário de dados (somente leitura, usuário do agente)."""

import re

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from pydantic import BaseModel

from srag_agente.rag.embeddings import Embedder

_SQL_BUSCA = (
    "SELECT campo, pagina, texto, embedding <=> %s AS distancia "
    "FROM gold.dicionario_trechos ORDER BY distancia LIMIT %s"
)
_SQL_POR_CAMPO = (
    "SELECT campo, pagina, texto, 0.0 AS distancia "
    "FROM gold.dicionario_trechos WHERE campo = ANY(%s) ORDER BY id LIMIT %s"
)
_IDENTIFICADOR = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")


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
        """Busca híbrida: campos citados pelo nome primeiro, o resto por similaridade."""
        nomes = _IDENTIFICADOR.findall(pergunta)
        exatos = self._consultar(_SQL_POR_CAMPO, (nomes, self._k)) if nomes else []
        if len(exatos) >= self._k:
            return exatos
        vetor = np.array(self._embedder([pergunta])[0])
        vistos = {(t.campo, t.pagina, t.texto) for t in exatos}
        similares = self._consultar(_SQL_BUSCA, (vetor, self._k))
        extras = [t for t in similares if (t.campo, t.pagina, t.texto) not in vistos]
        return (exatos + extras)[: self._k]

    def _consultar(self, sql: str, parametros: tuple) -> list[TrechoDicionario]:
        return [
            TrechoDicionario(campo=campo, pagina=pagina, texto=texto, distancia=float(distancia))
            for campo, pagina, texto, distancia in self._conn.execute(sql, parametros).fetchall()
        ]
