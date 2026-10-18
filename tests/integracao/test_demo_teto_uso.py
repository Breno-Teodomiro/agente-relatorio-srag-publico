"""Leitura do uso diário da demonstração (`demo.teto`) contra o banco local da demo.

Roda no banco `srag_demo_teste` da fixture `banco_demo` (Postgres do Compose, nunca remoto).
As linhas semeadas usam o dono do cluster local para escolher `inicio` e `momento` (o auditor não
grava essas colunas) e são apagadas no fim; as contagens são conferidas como diferença em relação
ao que já havia, porque outros testes da demo também gravam execuções no mesmo banco.
"""

from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest

from srag_agente.banco import conectar
from srag_agente.demo import FUSO_DEMO, HOSTS_LOCAIS, SQLSTATE_TETO, teto
from srag_agente.erros import classificar

pytestmark = pytest.mark.integracao


def _cfg_demo(cfg_teste, banco_demo):
    # A fixture pode entregar o Config do banco da demo ou só o nome do banco.
    if isinstance(banco_demo, str):
        return cfg_teste.model_copy(update={"pg_banco": banco_demo})
    return banco_demo


@pytest.fixture
def cfg_demo(cfg_teste, banco_demo):
    cfg = _cfg_demo(cfg_teste, banco_demo)
    assert cfg.pg_banco != cfg_teste.pg_banco  # nunca semear linhas no banco de teste comum
    assert cfg.pg_host in HOSTS_LOCAIS and cfg.pg_host == cfg_teste.pg_host  # nunca remoto
    return cfg


@pytest.fixture
def dono(cfg_teste, cfg_demo):
    """Conexão do dono do cluster local no banco da demo, para semear e limpar."""
    with conectar(cfg_teste, "admin", banco=cfg_demo.pg_banco, autocommit=True) as conn:
        yield conn


@pytest.fixture
def limites_preservados(dono):
    """Guarda a linha de limites e a devolve no fim, qualquer que seja o desfecho do teste."""
    antes = dono.execute(
        "SELECT relatorios_dia, buscas_dia, custo_usd_dia, fuso FROM auditoria.limites_demo "
        "WHERE id = 1"
    ).fetchone()
    assert antes is not None, "a carga da demo devia ter semeado auditoria.limites_demo"
    yield antes
    dono.execute(
        "INSERT INTO auditoria.limites_demo (id, relatorios_dia, buscas_dia, custo_usd_dia, fuso) "
        "VALUES (1, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
        "relatorios_dia = EXCLUDED.relatorios_dia, buscas_dia = EXCLUDED.buscas_dia, "
        "custo_usd_dia = EXCLUDED.custo_usd_dia, fuso = EXCLUDED.fuso",
        antes,
    )


def _desde(dono) -> object:
    return dono.execute(
        "SELECT date_trunc('day', now() AT TIME ZONE %s) AT TIME ZONE %s", (FUSO_DEMO, FUSO_DEMO)
    ).fetchone()[0]


def _execucao(dono, inicio, criadas: list) -> str:
    id_execucao = str(uuid4())
    dono.execute(
        "INSERT INTO auditoria.execucoes (id_execucao, inicio, provedor, modelo, parametros, "
        "versao_codigo, hash_prompts) VALUES (%s, %s, 'teste', 'teste', '{}', 'teste', 'teste')",
        (id_execucao, inicio),
    )
    criadas.append(id_execucao)
    return id_execucao


def _encerrar(dono, id_execucao: str, status: str, custo: float) -> None:
    dono.execute(
        "UPDATE auditoria.execucoes SET status = %s, custo_usd = %s WHERE id_execucao = %s",
        (status, custo, id_execucao),
    )


def _evento(dono, id_execucao, sequencia, momento, ferramenta, tipo="ferramenta") -> None:
    dono.execute(
        "INSERT INTO auditoria.eventos (id_execucao, sequencia, momento, no, tipo, ferramenta) "
        "VALUES (%s, %s, %s, 'teste', %s, %s)",
        (id_execucao, sequencia, momento, tipo, ferramenta),
    )


@pytest.fixture
def criadas(dono):
    ids: list[str] = []
    yield ids
    if ids:
        dono.execute("DELETE FROM auditoria.eventos WHERE id_execucao = ANY(%s::uuid[])", (ids,))
        dono.execute("DELETE FROM auditoria.execucoes WHERE id_execucao = ANY(%s::uuid[])", (ids,))


def test_uso_conta_o_dia_de_brasilia_e_renova_a_meia_noite(cfg_demo, dono, limites_preservados,
                                                           criadas):  # fmt: skip
    dono.execute("UPDATE auditoria.limites_demo SET relatorios_dia = 100000 WHERE id = 1")
    antes = teto.uso_de_hoje(cfg_demo)
    desde = _desde(dono)
    um_tique = timedelta(microseconds=1)

    ontem = _execucao(dono, desde - um_tique, criadas)  # fora do dia
    _evento(dono, ontem, 1, desde - um_tique, "buscar_noticias")
    _encerrar(dono, ontem, "ok", 0.5)

    na_virada = _execucao(dono, desde, criadas)  # exatamente meia-noite: conta
    _evento(dono, na_virada, 1, desde, "buscar_noticias")
    _evento(dono, na_virada, 2, desde, "buscar_noticias")
    _evento(dono, na_virada, 3, desde, "consultar_metricas")  # outra ferramenta não conta
    # busca recusada pelo teto da execução ("Não executada"): evento `decisao`, não conta
    _evento(dono, na_virada, 4, desde, "buscar_noticias", tipo="decisao")
    _encerrar(dono, na_virada, "ok", 0.01)

    com_erro = _execucao(dono, desde + timedelta(minutes=1), criadas)
    _evento(dono, com_erro, 1, desde + timedelta(minutes=1), "buscar_noticias")
    _encerrar(dono, com_erro, "erro", 0.02)  # erro também gastou crédito: conta

    _execucao(dono, desde + timedelta(minutes=2), criadas)  # aberta

    depois = teto.uso_de_hoje(cfg_demo)
    assert depois.relatorios - antes.relatorios == 3
    assert depois.abertas - antes.abertas == 1
    assert depois.buscas - antes.buscas == 3
    assert depois.custo_usd - antes.custo_usd == pytest.approx(0.03)

    renova = depois.renova_em.astimezone(ZoneInfo(FUSO_DEMO))
    assert (renova.hour, renova.minute, renova.second, renova.microsecond) == (0, 0, 0, 0)
    assert depois.renova_em == desde + timedelta(days=1)


def test_uso_le_os_limites_da_tabela(cfg_demo, dono, limites_preservados):
    dono.execute(
        "UPDATE auditoria.limites_demo SET relatorios_dia = 7, buscas_dia = 21, "
        "custo_usd_dia = 0.5 WHERE id = 1"
    )
    uso = teto.uso_de_hoje(cfg_demo)
    assert (uso.relatorios_dia, uso.buscas_dia, uso.custo_usd_dia) == (7, 21, 0.5)


def test_sem_linha_de_limites_nao_executa(cfg_demo, dono, limites_preservados):
    dono.execute("DELETE FROM auditoria.limites_demo WHERE id = 1")
    with pytest.raises(teto.TetoIndisponivelError):
        teto.uso_de_hoje(cfg_demo)
    with pytest.raises(teto.TetoIndisponivelError):
        teto.conferir_teto(cfg_demo)
    assert "limites" in " ".join(teto.prontidao_banco_demo(cfg_demo))


def test_prontidao_ve_o_gatilho_e_percebe_quando_desligado(cfg_demo, dono):
    assert teto.prontidao_banco_demo(cfg_demo) == []  # o auditor enxerga pg_trigger
    dono.execute(f"ALTER TABLE auditoria.execucoes DISABLE TRIGGER {teto.GATILHO_TETO}")
    try:
        problemas = teto.prontidao_banco_demo(cfg_demo)
    finally:
        dono.execute(f"ALTER TABLE auditoria.execucoes ENABLE TRIGGER {teto.GATILHO_TETO}")
    assert any(teto.GATILHO_TETO in p for p in problemas)


def test_recusa_do_gatilho_chega_como_a_mensagem_do_teto(cfg_demo, dono, limites_preservados,
                                                        criadas):  # fmt: skip
    dono.execute("UPDATE auditoria.limites_demo SET relatorios_dia = 100000 WHERE id = 1")
    _encerrar(dono, _execucao(dono, _desde(dono), criadas), "ok", 0)  # ao menos uma hoje
    feitas = teto.uso_de_hoje(cfg_demo).relatorios
    dono.execute("UPDATE auditoria.limites_demo SET relatorios_dia = %s WHERE id = 1", (feitas,))
    tentativa = str(uuid4())
    criadas.append(tentativa)  # se a recusa falhar, a linha some no fim do teste
    with (
        conectar(cfg_demo, "auditor", autocommit=True) as conn,
        pytest.raises(psycopg.Error) as recusa,
    ):
        conn.execute(
            "INSERT INTO auditoria.execucoes (id_execucao, provedor, modelo, parametros, "
            "versao_codigo, hash_prompts) VALUES (%s, 'p', 'm', '{}', 'v', 'h')",
            (tentativa,),
        )
    assert recusa.value.sqlstate == SQLSTATE_TETO
    assert classificar(recusa.value, publico=True)[1] == teto.MENSAGEM_TETO_BANCO
    assert teto.conferir_teto(cfg_demo) is not None  # a tela também já recusaria
