"""`python -m srag_agente.dados`: carga completa, tudo ou nada."""

import pytest

from srag_agente.banco import conectar
from srag_agente.dados import __main__ as carga
from tests.dados.amostra import escrever_csv

pytestmark = pytest.mark.integracao


def _totais(cfg) -> tuple[int, int]:
    with conectar(cfg, "etl") as conn:
        return conn.execute(
            "SELECT (SELECT count(*) FROM raw.srag), (SELECT total_casos FROM gold.referencia)"
        ).fetchone()


def test_carga_completa_chega_ate_a_gold(cfg_teste, banco_carregado, tmp_path):
    csv = escrever_csv(tmp_path / "amostra.csv", banco_carregado)
    cfg = cfg_teste.model_copy(update={"arquivo_dados": csv})
    assert carga.executar_carga(cfg) == len(banco_carregado)
    assert _totais(cfg) == (len(banco_carregado), len(banco_carregado))


def test_carga_que_falha_na_transformacao_desfaz_a_raw(
    cfg_teste, banco_carregado, tmp_path, monkeypatch
):
    # Raw nova com 10 linhas + transformação quebrada: nada muda, nem a raw.
    csv = escrever_csv(tmp_path / "parcial.csv", banco_carregado[:10])
    cfg = cfg_teste.model_copy(update={"arquivo_dados": csv})

    def quebra(conn):
        raise RuntimeError("falha simulada na transformação")

    monkeypatch.setattr(carga, "transformar", quebra)
    with pytest.raises(RuntimeError, match="simulada"):
        carga.executar_carga(cfg)
    assert _totais(cfg) == (len(banco_carregado), len(banco_carregado))
