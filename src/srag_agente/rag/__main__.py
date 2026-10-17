"""`python -m srag_agente.rag` — indexa o dicionário real com embeddings locais."""

from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.rag.embeddings import criar_embedder
from srag_agente.rag.indexar import campos_da_raw, indexar_dicionario

if __name__ == "__main__":
    cfg = Config()
    with conectar(cfg, "etl", autocommit=True) as conn:
        total = indexar_dicionario(
            conn,
            cfg.arquivo_dicionario,
            criar_embedder(cfg.pasta_cache_modelos),
            campos_da_raw(conn),
        )
    print(f"Trechos indexados: {total}")
