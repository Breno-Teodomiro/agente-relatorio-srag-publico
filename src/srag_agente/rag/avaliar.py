"""`python -m srag_agente.rag.avaliar` — mede a busca no dicionário com o modelo real.

Dois conjuntos (pergunta -> campo esperado), cada um com acerto no 1º lugar e entre os 3
primeiros: o original e o de validação, escrito antes de qualquer ajuste e nunca usado para
escolher o ajuste (anti-sobreajuste, ADR 0006). Mudança na busca só vale se melhora a validação.
Usa o índice já gravado (`make indexar`) do banco de PG_BANCO.
"""

import json
from pathlib import Path

from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.ferramentas.dicionario import BuscaDicionario
from srag_agente.rag.embeddings import criar_embedder

CONJUNTOS = {
    "original": Path("tests/dados/perguntas_rag.json"),
    "validação": Path("tests/dados/perguntas_rag_validacao.json"),
}


def avaliar(busca: BuscaDicionario, casos: list[dict[str, str]]) -> tuple[int, int]:
    primeiro = top3 = 0
    for caso in casos:
        campos = [t.campo for t in busca.buscar(caso["pergunta"])]
        primeiro += campos[:1] == [caso["campo"]]
        top3 += caso["campo"] in campos[:3]
        if caso["campo"] not in campos[:1]:
            print(f"  errou: {caso['pergunta']!r} esperado={caso['campo']} veio={campos}")
    return primeiro, top3


if __name__ == "__main__":
    cfg = Config()
    with conectar(cfg, "agente", autocommit=True) as conn:
        busca = BuscaDicionario(conn, criar_embedder(cfg.pasta_cache_modelos), k=3)
        for nome, arquivo in CONJUNTOS.items():
            casos = json.loads(arquivo.read_text(encoding="utf-8"))
            print(f"== Conjunto {nome} ({arquivo})")
            primeiro, top3 = avaliar(busca, casos)
            total = len(casos)
            print(f"Perguntas: {total}")
            print(f"Acerto no 1º lugar: {primeiro}/{total}")
            print(f"Acerto entre os 3 primeiros: {top3}/{total}")
