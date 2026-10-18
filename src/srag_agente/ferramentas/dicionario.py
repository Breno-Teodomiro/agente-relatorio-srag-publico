"""Busca no dicionário de dados (somente leitura, usuário do agente).

Dois métodos para o que não é citado pelo nome: `vetor` (embeddings, o padrão) e `texto` (busca de
texto completo do Postgres em português, sem carregar o modelo; usada onde a memória é curta).
"""

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
# Busca de texto: `plainto_tsquery` normaliza a pergunta (operadores viram dado) e o `replace` troca
# o E entre os termos por OU, senão uma pergunta em linguagem natural quase nunca casa. A pergunta
# vai sempre como parâmetro; o `replace` age só sobre a saída já normalizada. Sem `setweight`, o
# `ts_rank` fica abaixo de ~0,1, então a distância nunca chega a 0 (0.0 é o selo de "nome exato").
_SQL_TEXTO = (
    "SELECT campo, pagina, texto, "
    "1 - ts_rank(to_tsvector('portuguese', texto), q) AS distancia "
    "FROM gold.dicionario_trechos, "
    "(SELECT replace(plainto_tsquery('portuguese', %s)::text, '&', '|')::tsquery AS q) AS consulta "
    "WHERE to_tsvector('portuguese', texto) @@ q "
    "ORDER BY ts_rank(to_tsvector('portuguese', texto), q) DESC, id LIMIT %s"
)
_IDENTIFICADOR = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")


class TrechoDicionario(BaseModel):
    campo: str | None
    pagina: int
    texto: str
    distancia: float


class BuscaDicionario:
    def __init__(self, conn: psycopg.Connection, embedder: Embedder | None, k: int = 3) -> None:
        self._conn = conn
        self._embedder = embedder
        self._k = k
        self.metodo = "vetor" if embedder is not None else "texto"
        if embedder is not None:
            register_vector(conn)

    def buscar(self, pergunta: str) -> list[TrechoDicionario]:
        """Busca híbrida: campos citados pelo nome primeiro, o resto por similaridade."""
        nomes = _IDENTIFICADOR.findall(pergunta)
        exatos = self._consultar(_SQL_POR_CAMPO, (nomes, self._k)) if nomes else []
        if len(exatos) >= self._k:
            return exatos
        vistos = {(t.campo, t.pagina, t.texto) for t in exatos}
        similares = self._similares(pergunta)
        extras = [t for t in similares if (t.campo, t.pagina, t.texto) not in vistos]
        return (exatos + extras)[: self._k]

    def _similares(self, pergunta: str) -> list[TrechoDicionario]:
        if self._embedder is None:
            return self._consultar(_SQL_TEXTO, (pergunta, self._k))
        vetor = np.array(self._embedder([pergunta])[0])
        return self._consultar(_SQL_BUSCA, (vetor, self._k))

    def _consultar(self, sql: str, parametros: tuple) -> list[TrechoDicionario]:
        return [
            TrechoDicionario(campo=campo, pagina=pagina, texto=texto, distancia=float(distancia))
            for campo, pagina, texto, distancia in self._conn.execute(sql, parametros).fetchall()
        ]
