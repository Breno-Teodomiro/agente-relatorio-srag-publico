"""Séries dos dois gráficos obrigatórios e distribuições de perfil dos casos."""

from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel

from srag_agente.metricas.repositorio import DadosIndisponiveis, Referencia
from srag_agente.metricas.supressao import precisa_suprimir, suprimir

TipoSerie = Literal["diaria_30d", "mensal_12m"]
Dimensao = Literal["classificacao", "faixa_etaria"]

NOTAS: dict[str, str] = {
    "diaria_30d": "Últimos 30 dias até a data de referência. Os 14 dias mais recentes ainda estão "
    "em consolidação (notificações atrasadas).",
    "mensal_12m": "Últimos 12 meses até a data de referência. Meses anteriores ao início da base "
    "aparecem sem dados; o primeiro e o último mês podem estar incompletos.",
}


class FonteSeries(Protocol):
    def referencia(self) -> Referencia: ...
    def serie_diaria(self, uf: str) -> list[dict]: ...
    def serie_mensal(self, uf: str) -> list[dict]: ...
    def distribuicao(self, dimensao: str, uf: str) -> list[dict]: ...


class PontoSerie(BaseModel):
    rotulo: str
    casos: int | str | None
    em_consolidacao: bool = False
    fora_da_base: bool = False
    parcial: bool = False


class Serie(BaseModel):
    tipo: TipoSerie
    uf: str
    data_referencia: date
    pontos: list[PontoSerie]
    total: int | str
    nota: str


class CategoriaDistribuicao(BaseModel):
    categoria: str
    casos: int | str
    percentual: float | None


class Distribuicao(BaseModel):
    dimensao: Dimensao
    uf: str
    total: int | str
    categorias: list[CategoriaDistribuicao]


def _ponto(linha: dict, tipo: TipoSerie) -> PontoSerie:
    casos = linha["casos"]
    if tipo == "diaria_30d":
        return PontoSerie(
            rotulo=linha["dia"].isoformat(),
            casos=suprimir(casos),
            em_consolidacao=linha["em_consolidacao"],
        )
    return PontoSerie(
        rotulo=linha["mes"],
        casos=None if casos is None else suprimir(casos),
        fora_da_base=linha["fora_da_base"],
        parcial=linha["parcial"],
    )


def calcular_serie(tipo: TipoSerie, fonte: FonteSeries, uf: str = "BR") -> Serie:
    referencia = fonte.referencia()
    linhas = fonte.serie_diaria(uf) if tipo == "diaria_30d" else fonte.serie_mensal(uf)
    total = sum(linha["casos"] or 0 for linha in linhas)
    return Serie(
        tipo=tipo,
        uf=uf,
        data_referencia=referencia.data_referencia,
        pontos=[_ponto(linha, tipo) for linha in linhas],
        total=suprimir(total),
        nota=NOTAS[tipo],
    )


def calcular_distribuicao(dimensao: Dimensao, fonte: FonteSeries, uf: str = "BR") -> Distribuicao:
    linhas = fonte.distribuicao(dimensao, uf)
    if not linhas:
        raise DadosIndisponiveis(f"sem casos para a UF {uf}")
    total = sum(linha["casos"] for linha in linhas)
    return Distribuicao(
        dimensao=dimensao,
        uf=uf,
        total=suprimir(total),
        categorias=[
            CategoriaDistribuicao(
                categoria=linha["categoria"],
                casos=suprimir(linha["casos"]),
                percentual=None if precisa_suprimir(linha["casos"]) else linha["casos"] / total,
            )
            for linha in linhas
        ],
    )
