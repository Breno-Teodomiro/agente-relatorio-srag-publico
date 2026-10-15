"""`python -m srag_agente.dados`: carga completa, tudo ou nada."""

import threading
import time

import pytest

from srag_agente.banco import conectar
from srag_agente.dados import __main__ as carga
from srag_agente.dados.transformar import TRAVA_TRANSFORMACAO, transformar
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


# --- Carga e transformar ao mesmo tempo -------------------------------------------------------
# Ordem de travas única nos dois caminhos: consultiva → raw → silver/gold. Com a ordem invertida
# (carga trava a raw e só depois a consultiva), cada um espera o outro: DeadlockDetected.

# A carga já está na fila certa: ou segura a raw (ordem antiga) ou espera a consultiva (nova).
CARGA_EM_ANDAMENTO = """
SELECT EXISTS (
  SELECT 1 FROM pg_locks
  WHERE pid <> pg_backend_pid() AND database = (SELECT oid FROM pg_database
                                                 WHERE datname = current_database())
    AND ((locktype = 'relation' AND relation = 'raw.srag'::regclass
          AND mode = 'AccessExclusiveLock' AND granted)
      OR (locktype = 'advisory' AND objid = %s AND NOT granted))
)
"""


def _rodar(erros: list[BaseException], alvo, *args) -> threading.Thread:
    def corpo() -> None:
        try:
            alvo(*args)
        except BaseException as erro:  # o teste quer ver qualquer falha da thread
            erros.append(erro)

    thread = threading.Thread(target=corpo)
    thread.start()
    return thread


def _estado_consistente(cfg, esperado: int) -> None:
    with conectar(cfg, "etl") as conn:
        raw, silver, gold = conn.execute(
            "SELECT (SELECT count(*) FROM raw.srag), (SELECT count(*) FROM silver.casos), "
            "(SELECT total_casos FROM gold.referencia)"
        ).fetchone()
    assert raw == silver == gold == esperado


def test_carga_e_transformar_na_ordem_mais_perigosa_nao_travam(
    cfg_teste, banco_carregado, tmp_path
):
    # Força o entrelaçamento que causava o impasse: o transformar avulso pega a consultiva
    # primeiro, espera a carga entrar na fila e só então lê a raw.
    csv = escrever_csv(tmp_path / "amostra.csv", banco_carregado)
    cfg = cfg_teste.model_copy(update={"arquivo_dados": csv})
    erros: list[BaseException] = []

    def transformar_avulso(liberar_carga: threading.Event) -> None:
        with conectar(cfg, "etl") as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (TRAVA_TRANSFORMACAO,))
            liberar_carga.set()
            limite = time.monotonic() + 10
            while not conn.execute(CARGA_EM_ANDAMENTO, (TRAVA_TRANSFORMACAO,)).fetchone()[0]:
                assert time.monotonic() < limite, "a carga não chegou à fila de travas"
                time.sleep(0.05)
            transformar(conn)  # mesma sessão: a consultiva é reentrante
            conn.commit()

    liberar_carga = threading.Event()
    threads = [_rodar(erros, transformar_avulso, liberar_carga)]
    assert liberar_carga.wait(10)
    threads.append(_rodar(erros, carga.executar_carga, cfg))
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "carga e transformar travaram"
    assert erros == []
    _estado_consistente(cfg, len(banco_carregado))


@pytest.mark.parametrize("rodada", range(3))
def test_carga_e_transformar_soltos_ao_mesmo_tempo(cfg_teste, banco_carregado, tmp_path, rodada):
    csv = escrever_csv(tmp_path / "amostra.csv", banco_carregado)
    cfg = cfg_teste.model_copy(update={"arquivo_dados": csv})
    erros: list[BaseException] = []
    largada = threading.Barrier(2)

    def com_largada(alvo) -> None:
        if alvo is transformar:
            with conectar(cfg, "etl", autocommit=True) as conn:
                largada.wait()
                transformar(conn)
        else:
            largada.wait()
            carga.executar_carga(cfg)

    threads = [_rodar(erros, com_largada, alvo) for alvo in (transformar, carga.executar_carga)]
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "carga e transformar travaram"
    assert erros == []
    _estado_consistente(cfg, len(banco_carregado))
