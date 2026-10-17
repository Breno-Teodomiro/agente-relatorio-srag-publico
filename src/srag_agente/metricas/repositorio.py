"""Única porta do agente para o banco: SQL fixo, parâmetros por bind, usuário somente leitura."""

from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg.errors import QueryCanceled
from psycopg.rows import dict_row


class DadosIndisponiveis(LookupError):  # noqa: N818 (nome fixado pelo plano; não é "erro" de programa)
    """Não há dados para o recorte pedido (ex.: UF sem casos na base)."""


@dataclass(frozen=True)
class Referencia:
    data_referencia: date
    data_consolidada: date
    inicio_base: date
    total_casos: int


# As travas do papel (somente leitura, statement_timeout) são só default de sessão: cada consulta
# reafirma as suas dentro da própria transação. Os dois comandos abaixo são fixos; nada de fora
# chega a eles.
_SQL_SOMENTE_LEITURA = "SET TRANSACTION READ ONLY"
_SQL_TEMPO_MAXIMO = "SET LOCAL statement_timeout = '5s'"
TENTATIVAS = 3  # a transformação segura a gold por alguns segundos; o cancelamento é transitório


_SQL_REFERENCIA = (
    "SELECT data_referencia, data_consolidada, inicio_base, total_casos FROM gold.referencia"
)
_SQL_INDICADORES = "SELECT * FROM gold.indicadores WHERE uf = %(uf)s"
_SQL_JANELAS = "SELECT * FROM gold.janelas_aumento WHERE uf = %(uf)s"
_SQL_SERIE_DIARIA = """
SELECT g.dia::date AS dia,
       coalesce(d.casos, 0) AS casos,
       g.dia::date > r.data_consolidada AS em_consolidacao
FROM gold.referencia AS r
CROSS JOIN LATERAL generate_series(r.data_referencia - 29, r.data_referencia, interval '1 day')
  AS g(dia)
LEFT JOIN gold.casos_diarios AS d ON d.dia = g.dia::date AND d.uf = %(uf)s
ORDER BY g.dia
"""
_SQL_SERIE_MENSAL = """
SELECT to_char(g.mes, 'YYYY-MM') AS mes,
       CASE WHEN g.mes < date_trunc('month', r.inicio_base) THEN NULL
            ELSE coalesce(sum(d.casos), 0)::int END AS casos,
       g.mes < date_trunc('month', r.inicio_base) AS fora_da_base,
       (g.mes = date_trunc('month', r.data_referencia)
        OR (g.mes = date_trunc('month', r.inicio_base) AND r.inicio_base > g.mes::date))
         AS parcial
FROM gold.referencia AS r
CROSS JOIN LATERAL generate_series(
  date_trunc('month', r.data_referencia) - interval '11 months',
  date_trunc('month', r.data_referencia),
  interval '1 month') AS g(mes)
LEFT JOIN gold.casos_diarios AS d
  ON date_trunc('month', d.dia) = g.mes AND d.uf = %(uf)s
GROUP BY g.mes, r.inicio_base, r.data_referencia
ORDER BY g.mes
"""
_SQL_DISTRIBUICAO = (
    "SELECT categoria, casos FROM gold.distribuicao "
    "WHERE dimensao = %(dimensao)s AND uf = %(uf)s ORDER BY casos DESC, categoria"
)


class RepositorioGold:
    def __init__(self, conn: psycopg.Connection) -> None:
        if not conn.autocommit:
            raise ValueError("RepositorioGold exige conexão em autocommit (a transação é dele)")
        self._conn = conn

    def _tentar(self, consulta: str, parametros: dict) -> list[dict]:
        with self._conn.transaction():
            self._conn.execute(_SQL_SOMENTE_LEITURA)
            self._conn.execute(_SQL_TEMPO_MAXIMO)
            with self._conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(consulta, parametros)
                return cursor.fetchall()

    def _linhas(self, consulta: str, parametros: dict | None = None) -> list[dict]:
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                return self._tentar(consulta, parametros or {})
            except QueryCanceled:
                if tentativa == TENTATIVAS:
                    raise
        raise AssertionError("inalcançável")  # pragma: no cover

    def _uma(self, consulta: str, parametros: dict | None, sem_dados: str) -> dict:
        linhas = self._linhas(consulta, parametros)
        if not linhas:
            raise DadosIndisponiveis(sem_dados)
        return linhas[0]

    def referencia(self) -> Referencia:
        linha = self._uma(_SQL_REFERENCIA, None, "base vazia")
        if linha["data_referencia"] is None:
            raise DadosIndisponiveis("base sem datas de primeiros sintomas")
        return Referencia(**linha)

    def indicadores(self, uf: str) -> dict:
        return self._uma(_SQL_INDICADORES, {"uf": uf}, f"sem casos para a UF {uf}")

    def janelas_aumento(self, uf: str) -> dict:
        return self._uma(_SQL_JANELAS, {"uf": uf}, f"sem casos para a UF {uf}")

    def serie_diaria(self, uf: str) -> list[dict]:
        return self._linhas(_SQL_SERIE_DIARIA, {"uf": uf})

    def serie_mensal(self, uf: str) -> list[dict]:
        return self._linhas(_SQL_SERIE_MENSAL, {"uf": uf})

    def distribuicao(self, dimensao: str, uf: str) -> list[dict]:
        return self._linhas(_SQL_DISTRIBUICAO, {"dimensao": dimensao, "uf": uf})
