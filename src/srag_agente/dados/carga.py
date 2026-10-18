"""Carga completa (`srag carga`): bootstrap → raw → silver → gold → auditoria."""

from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.dados.bootstrap import preparar_banco
from srag_agente.dados.carga_raw import carregar_raw
from srag_agente.dados.transformar import transformar, travar_transformacao


def executar_carga(cfg: Config) -> int:
    """Recarrega a raw e reconstrói as camadas numa transação só: tudo ou nada.

    Recarregar só a raw deixaria a silver e a gold desatualizadas: por isso a carga e o
    `transformar` andam sempre juntos. Se a transformação falhar, a raw nova também é desfeita e
    a gold continua coerente com a raw anterior. A auditoria não é apagada (o 04 só cria o que
    falta).
    """
    preparar_banco(cfg, cfg.pg_banco)
    with conectar(cfg, "etl") as conn, conn.transaction():
        travar_transformacao(conn)  # antes da raw: mesma ordem de travas do `transformar`
        total = carregar_raw(conn, cfg.arquivo_dados)
        transformar(conn)
    return total
