"""Embeddings locais e gratuitos (fastembed/ONNX): sem chave, reproduzível por quem avaliar."""

from collections.abc import Callable
from pathlib import Path

Embedder = Callable[[list[str]], list[list[float]]]
MODELO_EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSAO = 384


def criar_embedder(pasta_cache: Path) -> Embedder:
    from fastembed import TextEmbedding  # import tardio: carregar o modelo é lento

    modelo = TextEmbedding(MODELO_EMBEDDING, cache_dir=str(pasta_cache))

    def embutir(textos: list[str]) -> list[list[float]]:
        return [vetor.tolist() for vetor in modelo.embed(textos)]

    return embutir
