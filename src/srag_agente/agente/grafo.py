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
    return _com_texto_legivel(construir_grafo(vazias).get_graph().draw_mermaid())


# O estilo padrão do LangGraph pinta a caixa de claro mas deixa o texto na cor do tema da página:
# no tema escuro do GitHub o nome do nó fica claro sobre claro e some. Fixa texto e borda.
_ESTILOS = {
    "classDef default fill:#f2f0ff,line-height:1.2": (
        "classDef default fill:#f2f0ff,stroke:#5b4fcf,color:#1a1a2e,line-height:1.2"
    ),
    "classDef last fill:#bfb6fc": "classDef last fill:#bfb6fc,stroke:#5b4fcf,color:#1a1a2e",
}


def _com_texto_legivel(mermaid: str) -> str:
    for original, legivel in _ESTILOS.items():
        mermaid = mermaid.replace(original, legivel)
    return mermaid
