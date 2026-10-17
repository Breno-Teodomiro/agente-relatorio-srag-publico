"""`python -m srag_agente.rag.avaliar` — mede a busca no dicionário com o modelo real.

Lê tests/dados/perguntas_rag.json (pergunta -> campo esperado) e imprime o acerto no 1º lugar
e entre os 3 primeiros. Usa o índice já gravado (`make indexar`) do banco de PG_BANCO.
"""

import json
from pathlib import Path

from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.ferramentas.dicionario import BuscaDicionario
from srag_agente.rag.embeddings import criar_embedder

PERGUNTAS = Path("tests/dados/perguntas_rag.json")


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
    casos = json.loads(PERGUNTAS.read_text(encoding="utf-8"))
    with conectar(cfg, "agente", autocommit=True) as conn:
        busca = BuscaDicionario(conn, criar_embedder(cfg.pasta_cache_modelos), k=3)
        primeiro, top3 = avaliar(busca, casos)
    total = len(casos)
    print(f"Perguntas: {total}")
    print(f"Acerto no 1º lugar: {primeiro}/{total}")
    print(f"Acerto entre os 3 primeiros: {top3}/{total}")
