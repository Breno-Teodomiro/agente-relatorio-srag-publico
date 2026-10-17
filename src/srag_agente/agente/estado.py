"""Estado do grafo. Campos com reducer (operator.add / add_messages) acumulam entre os nós."""

import operator
from datetime import date
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from srag_agente.ferramentas.registro import Evidencia
from srag_agente.modelos import TextoRelatorio


class EstadoAgente(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    uf: str
    pergunta_extra: str | None
    data_execucao: date
    evidencias: Annotated[list[Evidencia], operator.add]
    chamadas_ferramenta: Annotated[int, operator.add]
    tokens_entrada: Annotated[int, operator.add]
    tokens_saida: Annotated[int, operator.add]
    voltas_cobertura: int
    rota_cobertura: str
    cobertura_incompleta: bool
    tentativas_redacao: int
    texto: TextoRelatorio | None
    violacoes: list[str]
    avisos: Annotated[list[str], operator.add]
    status: str  # em_andamento | ok | parcial | revisao_humana


def estado_inicial(uf: str, pergunta_extra: str | None, data_execucao: date) -> EstadoAgente:
    return EstadoAgente(
        messages=[],
        uf=uf,
        pergunta_extra=pergunta_extra,
        data_execucao=data_execucao,
        evidencias=[],
        chamadas_ferramenta=0,
        tokens_entrada=0,
        tokens_saida=0,
        voltas_cobertura=0,
        rota_cobertura="",
        cobertura_incompleta=False,
        tentativas_redacao=0,
        texto=None,
        violacoes=[],
        avisos=[],
        status="em_andamento",
    )
