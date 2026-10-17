"""Dublês de teste: substituem LLM, notícias e embeddings sem custo nem rede."""

import hashlib

DIMENSAO = 384


def embedder_falso(textos: list[str]) -> list[list[float]]:
    """Vetor determinístico por texto: textos iguais → distância zero."""
    vetores = []
    for texto in textos:
        semente = hashlib.sha256(texto.encode()).digest()
        vetores.append([semente[i % len(semente)] / 255 + 0.01 for i in range(DIMENSAO)])
    return vetores
