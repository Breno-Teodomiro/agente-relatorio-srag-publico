"""Montagem do StateGraph: o fluxo é explícito e auditável."""

from collections.abc import Sequence
from datetime import date

from langgraph.graph import END, START, StateGraph

from srag_agente.agente.estado import EstadoAgente, estado_inicial
from srag_agente.agente.nos import Dependencias, NosAgente
from srag_agente.guardrails.limites import Limites

LIMITE_RECURSAO = 60


def construir_grafo(deps: Dependencias):
    nos = NosAgente(deps)
    grafo = StateGraph(EstadoAgente)
    grafo.add_node("guardrail_entrada", nos.guardrail_entrada)
    grafo.add_node("orquestrador", nos.orquestrador)
    grafo.add_node("ferramentas", nos.ferramentas)
    grafo.add_node("verificar_cobertura", nos.verificar_cobertura)
    grafo.add_node("redator", nos.redator)
    grafo.add_node("guardrail_saida", nos.guardrail_saida)

    grafo.add_edge(START, "guardrail_entrada")
    grafo.add_edge("guardrail_entrada", "orquestrador")
    grafo.add_conditional_edges(
        "orquestrador",
        nos.rota_orquestrador,
        {"ferramentas": "ferramentas", "verificar_cobertura": "verificar_cobertura"},
    )
    grafo.add_conditional_edges(
        "ferramentas",
        nos.rota_ferramentas,
        {"orquestrador": "orquestrador", "verificar_cobertura": "verificar_cobertura"},
    )
    grafo.add_conditional_edges(
        "verificar_cobertura",
        nos.rota_cobertura,
        {"orquestrador": "orquestrador", "redator": "redator"},
    )
    grafo.add_edge("redator", "guardrail_saida")
    grafo.add_conditional_edges(
        "guardrail_saida", nos.rota_saida, {"redator": "redator", "fim": END}
    )
    return grafo.compile()


def executar_agente(
    deps: Dependencias,
    *,
    uf: str,
    pergunta_extra: str | None,
    data_execucao: date,
    callbacks: Sequence = (),
) -> EstadoAgente:
    return construir_grafo(deps).invoke(
        estado_inicial(uf, pergunta_extra, data_execucao),
        config={"recursion_limit": LIMITE_RECURSAO, "callbacks": list(callbacks)},
    )


def estrutura_mermaid() -> str:
    """Diagrama do grafo real, gerado do código (vai para docs/grafo_langgraph.mmd)."""
    vazias = Dependencias(
        modelo_orquestrador=None,
        redator=None,
        classificador=None,
        ferramentas={},
        auditoria=None,
        limites=Limites(),
    )
    return construir_grafo(vazias).get_graph().draw_mermaid()
