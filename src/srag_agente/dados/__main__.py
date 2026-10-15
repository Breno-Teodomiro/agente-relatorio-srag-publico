"""Carga completa: `python -m srag_agente.dados` (bootstrap → raw → silver → gold → auditoria)."""

from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.dados.bootstrap import preparar_banco
from srag_agente.dados.carga_raw import carregar_raw
from srag_agente.dados.transformar import transformar


def executar_carga(cfg: Config) -> int:
    """Recarrega a raw e reconstrói as camadas numa transação só: tudo ou nada.

    Se a transformação falhar, a raw nova também é desfeita e a gold continua coerente com a
    raw anterior. A auditoria não é apagada (o 04 só cria o que falta).
    """
    preparar_banco(cfg, cfg.pg_banco)
    with conectar(cfg, "etl") as conn, conn.transaction():
        total = carregar_raw(conn, cfg.arquivo_dados)
        transformar(conn)
    return total


if __name__ == "__main__":
    print(f"Linhas carregadas na raw: {executar_carga(Config()):,}".replace(",", "."))
