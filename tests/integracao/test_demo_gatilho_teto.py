"""Gatilho do teto diário: recusa com SR429, serializa entre conexões e conta o dia no fuso.

Banco descartável só deste módulo: os testes mudam limites e apagam a trilha.
"""

import threading
import time
from uuid import uuid4

import psycopg
import pytest
from psycopg import IsolationLevel

from srag_agente.banco import conectar
from srag_agente.demo import SQLSTATE_TETO
from srag_agente.demo.carregar import carregar
from tests.integracao.conftest import PREFIXO_DESCARTAVEL, apagar_banco_demo, criar_banco_demo

pytestmark = pytest.mark.integracao

INSERIR = (
    "INSERT INTO auditoria.execucoes (id_execucao, provedor, modelo, parametros, versao_codigo, "
    "hash_prompts) VALUES (%s, 'p', 'm', '{}', 'v', 'h')"
)  # as colunas que o auditor pode gravar (04_auditoria.sql)
DESDE = "date_trunc('day', now() AT TIME ZONE l.fuso) AT TIME ZONE l.fuso"


@pytest.fixture(scope="module")
def banco_teto(cfg_teste, carga_demo):
    nome = f"{PREFIXO_DESCARTAVEL}teto_{uuid4().hex[:8]}"
    cfg = criar_banco_demo(cfg_teste, nome)
    carregar(cfg, carga_demo.pacote, alvo=cfg.pg_host, permitir_local=True)
    yield cfg
    apagar_banco_demo(cfg_teste, nome)


@pytest.fixture
def teto(banco_teto):
    """Trilha vazia e limite ajustável; volta a semente no fim."""

    def ajustar(relatorios: int) -> None:
        with conectar(banco_teto, "admin", autocommit=True) as conn:
            conn.execute("UPDATE auditoria.limites_demo SET relatorios_dia = %s", (relatorios,))

    with conectar(banco_teto, "admin", autocommit=True) as conn:
        conn.execute("DELETE FROM auditoria.eventos")
        conn.execute("DELETE FROM auditoria.execucoes")
    yield ajustar
    with conectar(banco_teto, "admin", autocommit=True) as conn:
        conn.execute(
            "INSERT INTO auditoria.limites_demo VALUES (1, 10, 40, 1.00, 'America/Sao_Paulo') "
            "ON CONFLICT (id) DO UPDATE SET relatorios_dia = 10"
        )


def _iniciar(cfg) -> None:
    with conectar(cfg, "auditor", autocommit=True) as conn:
        conn.execute(INSERIR, (uuid4(),))


def _total(cfg) -> int:
    with conectar(cfg, "admin", autocommit=True) as conn:
        return conn.execute("SELECT count(*) FROM auditoria.execucoes").fetchone()[0]


def _recusado(acao) -> psycopg.Error:
    with pytest.raises(psycopg.Error) as erro:
        acao()
    assert erro.value.sqlstate == SQLSTATE_TETO
    return erro.value


def test_execucao_alem_do_limite_e_recusada_com_sr429(banco_teto, teto):
    teto(2)
    _iniciar(banco_teto)
    _iniciar(banco_teto)
    erro = _recusado(lambda: _iniciar(banco_teto))
    assert "limite diário de 2 relatórios" in erro.diag.message_primary
    assert _total(banco_teto) == 2  # a recusada não deixa linha


def test_toda_situacao_conta(banco_teto, teto):
    # execução que terminou em erro também gastou crédito
    teto(1)
    with conectar(banco_teto, "admin", autocommit=True) as conn:
        conn.execute(
            "INSERT INTO auditoria.execucoes (id_execucao, status, fim, provedor, modelo, "
            "parametros, versao_codigo, hash_prompts) VALUES (%s, 'erro', now(), 'p', 'm', "
            "'{}', 'v', 'h')",
            (uuid4(),),
        )
    _recusado(lambda: _iniciar(banco_teto))


def test_segunda_conexao_espera_a_primeira_e_e_recusada(banco_teto, teto):
    # Interleaving determinístico: a 1ª insere e segura a transação aberta; a 2ª tem de ESPERAR
    # a trava (sem ela, contaria 0 porque a 1ª ainda não confirmou, e as duas passariam).
    teto(1)
    resultado: dict[str, object] = {}
    with conectar(banco_teto, "auditor") as primeira:
        primeira.execute(INSERIR, (uuid4(),))

        def segunda():
            try:
                _iniciar(banco_teto)
                resultado["fim"] = "aceita"
            except psycopg.Error as erro:
                resultado["fim"] = erro.sqlstate

        linha = threading.Thread(target=segunda)
        linha.start()
        time.sleep(1.5)
        assert linha.is_alive(), "a segunda conexão não esperou a trava do teto"
        primeira.commit()
        linha.join(timeout=30)
    assert resultado["fim"] == SQLSTATE_TETO
    assert _total(banco_teto) == 1


def test_varias_ao_mesmo_tempo_so_uma_passa(banco_teto, teto):
    teto(1)
    largada = threading.Barrier(4)
    resultados: list[str] = []

    def tentar():
        with conectar(banco_teto, "auditor", autocommit=True) as conn:
            largada.wait()
            try:
                conn.execute(INSERIR, (uuid4(),))
                resultados.append("aceita")
            except psycopg.Error as erro:
                resultados.append(erro.sqlstate)

    linhas = [threading.Thread(target=tentar) for _ in range(4)]
    for linha in linhas:
        linha.start()
    for linha in linhas:
        linha.join(timeout=30)
    assert sorted(resultados) == sorted(["aceita"] + [SQLSTATE_TETO] * 3)
    assert _total(banco_teto) == 1


def test_retrato_fixo_e_recusado_mesmo_abaixo_do_limite(banco_teto, teto):
    # REPEATABLE READ contaria a partir de um retrato antigo e furaria o teto em corrida.
    teto(10)
    with conectar(banco_teto, "auditor") as conn:
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        _recusado(lambda: conn.execute(INSERIR, (uuid4(),)))
    assert _total(banco_teto) == 0


def test_varias_linhas_num_comando_so_respeitam_o_limite(banco_teto, teto):
    teto(2)
    with conectar(banco_teto, "auditor", autocommit=True) as conn:
        _recusado(lambda: conn.execute(
            "INSERT INTO auditoria.execucoes (id_execucao, provedor, modelo, parametros, "
            "versao_codigo, hash_prompts) SELECT gen_random_uuid(), 'p', 'm', '{}', 'v', 'h' "
            "FROM generate_series(1, 3)"))  # fmt: skip
    assert _total(banco_teto) == 0


def test_sem_linha_de_limites_recusa(banco_teto, teto):
    with conectar(banco_teto, "admin", autocommit=True) as conn:
        conn.execute("DELETE FROM auditoria.limites_demo")
    erro = _recusado(lambda: _iniciar(banco_teto))
    assert "sem limites" in erro.diag.message_primary


def _inserir_como_dono(cfg, inicio_sql: str) -> None:
    with conectar(cfg, "admin", autocommit=True) as conn:
        conn.execute(
            "INSERT INTO auditoria.execucoes (id_execucao, inicio, provedor, modelo, parametros, "
            f"versao_codigo, hash_prompts) SELECT %s, {inicio_sql}, 'p', 'm', '{{}}', 'v', 'h' "
            "FROM auditoria.limites_demo l",
            (uuid4(),),
        )


def test_execucao_de_ontem_nao_conta(banco_teto, teto):
    # um minuto antes da meia-noite de Brasília: é ontem, mesmo sendo "hoje" em UTC
    teto(1)
    _inserir_como_dono(banco_teto, f"{DESDE} - interval '1 minute'")
    _iniciar(banco_teto)
    _recusado(lambda: _iniciar(banco_teto))


def test_meia_noite_exata_ja_e_hoje(banco_teto, teto):
    teto(1)
    _inserir_como_dono(banco_teto, DESDE)
    _recusado(lambda: _iniciar(banco_teto))


# --- volume da trilha: limite no banco da demo (credencial do auditor vazada não lota o disco) --
EVENTO = (
    "INSERT INTO auditoria.eventos (id_execucao, sequencia, no, tipo, argumentos, resultado, "
    "motivo, erro) VALUES (%s, %s, 'n', 'ferramenta', %s, %s, %s, %s)"
)


def _execucao_aberta(cfg):
    id_execucao = uuid4()
    with conectar(cfg, "auditor", autocommit=True) as conn:
        conn.execute(INSERIR, (id_execucao,))
    return id_execucao


def _evento(cfg, id_execucao, sequencia=1, argumentos=None, resultado=None, motivo=None,
            erro=None):  # fmt: skip
    from psycopg.types.json import Jsonb

    with conectar(cfg, "auditor", autocommit=True) as conn:
        conn.execute(EVENTO, (id_execucao, sequencia, Jsonb(argumentos), Jsonb(resultado),
                              motivo, erro))  # fmt: skip


def test_evento_do_tamanho_real_entra(banco_teto, teto):
    # maior evento medido na trilha local tem ~2,4 KB; resumo truncado do registro: 2000 car.
    execucao = _execucao_aberta(banco_teto)
    texto = "á" * 2000  # 2 bytes por caractere: pior caso do texto em português
    _evento(banco_teto, execucao, 1, {"x": texto}, {"resumo_truncado": texto, "a": "b" * 120},
            texto, texto)  # fmt: skip
    _evento(banco_teto, execucao, 100)


@pytest.mark.parametrize(
    "campo,valor",
    [("sequencia", 101), ("sequencia", 0), ("argumentos", {"x": "a" * 9000}),
     ("resultado", {"x": "a" * 9000}), ("motivo", "a" * 9000), ("erro", "a" * 9000)],
    ids=["sequencia_101", "sequencia_0", "argumentos", "resultado", "motivo", "erro"],
)  # fmt: skip
def test_evento_grande_demais_e_recusado(banco_teto, teto, campo, valor):
    execucao = _execucao_aberta(banco_teto)
    with pytest.raises(psycopg.errors.CheckViolation):
        _evento(banco_teto, execucao, **{campo: valor})


def test_parametros_grandes_demais_sao_recusados(banco_teto, teto):
    from psycopg.types.json import Jsonb

    with (
        conectar(banco_teto, "auditor", autocommit=True) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        conn.execute(
            "INSERT INTO auditoria.execucoes (id_execucao, provedor, modelo, parametros, "
            "versao_codigo, hash_prompts) VALUES (%s, 'p', 'm', %s, 'v', 'h')",
            (uuid4(), Jsonb({"pergunta_extra": "a" * 5000})),
        )


def test_trava_presa_por_outra_conexao_nao_pendura_o_insert(banco_teto, teto):
    # Quem segurar a trava do teto não prende a tela: o INSERT desiste em ~5 s (55P03). O
    # INSERT roda em outra linha de execução com prazo: sem o lock_timeout ele penduraria.
    teto(10)
    resultado: dict[str, object] = {}

    def iniciar():
        try:
            _iniciar(banco_teto)
            resultado["fim"] = "aceita"
        except psycopg.Error as erro:
            resultado["fim"] = erro.sqlstate

    with conectar(banco_teto, "admin") as segura:
        segura.execute("SELECT pg_advisory_xact_lock(7350302)")
        linha = threading.Thread(target=iniciar, daemon=True)
        linha.start()
        linha.join(timeout=15)
        pendurado = linha.is_alive()
        segura.rollback()
    linha.join(timeout=30)
    assert not pendurado, "o INSERT ficou esperando a trava sem prazo"
    assert resultado["fim"] == "55P03"
    with conectar(banco_teto, "admin", autocommit=True) as conn:  # a que pendurou não fica
        conn.execute("DELETE FROM auditoria.execucoes")
