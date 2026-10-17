"""Séries dos dois gráficos obrigatórios e distribuições de perfil dos casos."""

from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel

from srag_agente.metricas.repositorio import DadosIndisponiveis, Referencia
from srag_agente.metricas.supressao import suprimir, suprimir_celulas

TipoSerie = Literal["diaria_30d", "mensal_12m"]
Dimensao = Literal["classificacao", "faixa_etaria"]

NOTA_SUPRESSAO = (
    'Contagens de 1 a 4 aparecem como "<5". Quando há alguma, a menor contagem exibida também '
    'aparece como "oculto" (supressão complementar): sem isso, o total menos as demais '
    "revelaria o valor escondido. O total é exato e inclui as células escondidas."
)

NOTAS: dict[str, str] = {
    "diaria_30d": "Últimos 30 dias até a data de referência. Os 14 dias mais recentes ainda estão "
    "em consolidação (notificações atrasadas). " + NOTA_SUPRESSAO,
    "mensal_12m": "Últimos 12 meses até a data de referência. Meses anteriores ao início da base "
    "aparecem sem dados; o primeiro e o último mês podem estar incompletos. " + NOTA_SUPRESSAO,
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
    nota: str = NOTA_SUPRESSAO


def _ponto(linha: dict, tipo: TipoSerie, casos: int | str | None) -> PontoSerie:
    """`casos` já vem suprimido: a supressão complementar depende da série inteira."""
    if tipo == "diaria_30d":
        return PontoSerie(
            rotulo=linha["dia"].isoformat(),
            casos=casos,
            em_consolidacao=linha["em_consolidacao"],
        )
    return PontoSerie(
        rotulo=linha["mes"],
        casos=casos,
        fora_da_base=linha["fora_da_base"],
        parcial=linha["parcial"],
    )


def calcular_serie(tipo: TipoSerie, fonte: FonteSeries, uf: str = "BR") -> Serie:
    referencia = fonte.referencia()
    linhas = fonte.serie_diaria(uf) if tipo == "diaria_30d" else fonte.serie_mensal(uf)
    total = sum(linha["casos"] or 0 for linha in linhas)
    exibidos = suprimir_celulas([linha["casos"] for linha in linhas])
    return Serie(
        tipo=tipo,
        uf=uf,
        data_referencia=referencia.data_referencia,
        pontos=[_ponto(linha, tipo, casos) for linha, casos in zip(linhas, exibidos, strict=True)],
        total=suprimir(total),
        nota=NOTAS[tipo],
    )


def calcular_distribuicao(dimensao: Dimensao, fonte: FonteSeries, uf: str = "BR") -> Distribuicao:
    linhas = fonte.distribuicao(dimensao, uf)
    if not linhas:
        raise DadosIndisponiveis(f"sem casos para a UF {uf}")
    total = sum(linha["casos"] for linha in linhas)
    exibidos = suprimir_celulas([linha["casos"] for linha in linhas])
    return Distribuicao(
        dimensao=dimensao,
        uf=uf,
        total=suprimir(total),
        categorias=[
            CategoriaDistribuicao(
                categoria=linha["categoria"],
                casos=casos,
                # Percentual de célula escondida revelaria a contagem (casos = percentual x total).
                percentual=linha["casos"] / total if isinstance(casos, int) else None,
            )
            for linha, casos in zip(linhas, exibidos, strict=True)
        ],
    )
