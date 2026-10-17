"""O que o redator recebe: só evidências já calculadas, nunca a conversa do orquestrador."""

from collections.abc import Iterable
from datetime import date

from pydantic import BaseModel

from srag_agente.ferramentas.registro import Evidencia
from srag_agente.modelos import NoticiaCitavel, numerar_noticias


class ContextoRedacao(BaseModel):
    uf: str
    data_execucao: date
    data_referencia: date | None
    metricas: list[dict]
    series: list[dict]
    distribuicoes: list[dict]
    noticias: list[NoticiaCitavel]
    trechos_dicionario: list[dict]
    pergunta_extra: str | None = None
    violacoes_anteriores: list[str] = []
    avisos: list[str] = []


def montar_contexto(
    evidencias: list[Evidencia],
    *,
    uf: str,
    data_execucao: date,
    pergunta_extra: str | None = None,
    violacoes_anteriores: Iterable[str] = (),
    avisos: Iterable[str] = (),
) -> ContextoRedacao:
    def dados(ferramenta: str) -> list[dict]:
        return [e.dados for e in evidencias if e.ferramenta == ferramenta]

    series = dados("consultar_serie")
    return ContextoRedacao(
        uf=uf,
        data_execucao=data_execucao,
        data_referencia=date.fromisoformat(series[0]["data_referencia"]) if series else None,
        metricas=dados("consultar_metricas"),
        series=series,
        distribuicoes=dados("consultar_distribuicao"),
        noticias=numerar_noticias(evidencias),
        trechos_dicionario=[t for d in dados("consultar_dicionario") for t in d["trechos"]],
        pergunta_extra=pergunta_extra,
        violacoes_anteriores=list(violacoes_anteriores),
        avisos=list(avisos),
    )
