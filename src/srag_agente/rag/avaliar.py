"""`python -m srag_agente.rag.avaliar [--modo vetor|texto|ambos]` — mede a busca no dicionário.

Dois conjuntos (pergunta -> campo esperado), cada um com acerto no 1º lugar e entre os 3
primeiros: o original e o de validação, escrito antes de qualquer ajuste e nunca usado para
escolher o ajuste (anti-sobreajuste, ADR 0006). Mudança na busca só vale se melhora a validação.
Usa o índice já gravado (`make indexar`) do banco de PG_BANCO. `vetor` (padrão) usa o modelo real;
`texto` mede a busca de texto completo, sem modelo; `ambos` imprime os dois e a diferença.
"""

import argparse
import json
from collections.abc import Sequence
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


Resultado = dict[str, tuple[int, int, int]]  # conjunto -> (1º lugar, top 3, total)


def medir(busca: BuscaDicionario) -> Resultado:
    """Roda os dois conjuntos com a busca dada, imprimindo os erros e o resumo de cada um."""
    resultado: Resultado = {}
    for nome, arquivo in CONJUNTOS.items():
        casos = json.loads(arquivo.read_text(encoding="utf-8"))
        print(f"== Conjunto {nome} ({arquivo})")
        primeiro, top3 = avaliar(busca, casos)
        total = len(casos)
        print(f"Perguntas: {total}")
        print(f"Acerto no 1º lugar: {primeiro}/{total}")
        print(f"Acerto entre os 3 primeiros: {top3}/{total}")
        resultado[nome] = (primeiro, top3, total)
    return resultado


def diferenca(vetor: Resultado, texto: Resultado) -> list[str]:
    """Linhas `texto - vetor` por conjunto (negativo: a busca por texto acerta menos)."""
    return [
        f"{nome}: 1º lugar {texto[nome][0] - vetor[nome][0]:+d}, "
        f"top 3 {texto[nome][1] - vetor[nome][1]:+d} (de {vetor[nome][2]})"
        for nome in vetor
    ]


def principal(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Mede a busca no dicionário de dados.")
    parser.add_argument("--modo", choices=("vetor", "texto", "ambos"), default="vetor")
    modo = parser.parse_args(argv).modo
    cfg = Config()
    modos = ("vetor", "texto") if modo == "ambos" else (modo,)
    resultados: dict[str, Resultado] = {}
    with conectar(cfg, "agente", autocommit=True) as conn:
        embedder = criar_embedder(cfg.pasta_cache_modelos) if "vetor" in modos else None
        for m in modos:
            if modo == "ambos":
                print(f"##### Busca: {m}")
            resultados[m] = medir(BuscaDicionario(conn, embedder if m == "vetor" else None, k=3))
    if modo == "ambos":
        print("##### Diferença (texto - vetor)")
        print("\n".join(diferenca(resultados["vetor"], resultados["texto"])))


if __name__ == "__main__":
    principal()
