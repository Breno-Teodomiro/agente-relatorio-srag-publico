"""Trilha no Postgres, provada pelo efeito: a linha no banco, o JSONL, o erro que o banco devolve.

Os ataques rodam com o papel `srag_auditor` (o mesmo do registro): o que ele não consegue, o
registro também não consegue, nem por engano nem por código adulterado.
"""

import json
import re
import threading
import time
from datetime import date
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors

from srag_agente.auditoria.registro import STATUS_FINAIS, AuditoriaPostgres
from srag_agente.banco import conectar
from srag_agente.dados.bootstrap import ler_sql
from srag_agente.dados.transformar import transformar, travar_transformacao

pytestmark = pytest.mark.integracao


def _esperando_trava(observador: psycopg.Connection, pid: int, segundos: float = 5) -> bool:
    """Espera (até `segundos`) a sessão `pid` ficar parada numa trava ainda não concedida."""
    for _ in range(int(segundos * 10)):
        if observador.execute(
            "SELECT count(*) > 0 FROM pg_locks WHERE pid = %s AND NOT granted", (pid,)
        ).fetchone()[0]:
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def ids_criados(conn_etl):
    """Execuções criadas pelo teste; só o dono (ETL) apaga, e só aqui na limpeza."""
    ids: list[UUID] = []
    yield ids
    conn_etl.rollback()
    for id_execucao in ids:
        conn_etl.execute("DELETE FROM auditoria.eventos WHERE id_execucao = %s", (id_execucao,))
        conn_etl.execute("DELETE FROM auditoria.execucoes WHERE id_execucao = %s", (id_execucao,))
    conn_etl.commit()


@pytest.fixture
def auditoria(conn_auditor, ids_criados) -> AuditoriaPostgres:
    a = AuditoriaPostgres(conn_auditor)
    ids_criados.append(
        a.iniciar_execucao(
            provedor="openrouter", modelo="m", parametros={"uf": "BR"},
            versao_codigo="abc", hash_prompts="h",
        )
    )  # fmt: skip
    return a


def _finalizar(a: AuditoriaPostgres, **extra) -> None:
    valores = {"status": "ok", "data_referencia": date(2025, 6, 25), "tokens_entrada": 10,
               "tokens_saida": 5, "custo_usd": 0.001} | extra  # fmt: skip
    a.finalizar_execucao(**valores)


def _execucao_aberta(conn_auditor, ids_criados) -> UUID:
    id_execucao = uuid4()
    conn_auditor.execute(
        "INSERT INTO auditoria.execucoes (id_execucao, provedor, modelo, parametros, "
        "versao_codigo, hash_prompts) VALUES (%s, 'teste', 'm', '{}', 'abc', 'h')",
        (id_execucao,),
    )
    ids_criados.append(id_execucao)
    return id_execucao


# --- Do plano --------------------------------------------------------------------------------


def test_eventos_chegam_ao_banco_na_hora(conn_auditor, conn_etl, ids_criados):
    a = AuditoriaPostgres(conn_auditor)
    id_execucao = a.iniciar_execucao(
        provedor="openrouter", modelo="m", parametros={"uf": "BR"},
        versao_codigo="abc", hash_prompts="h",
    )  # fmt: skip
    ids_criados.append(id_execucao)
    a.registrar(
        "ferramentas", "ferramenta", ferramenta="consultar_metricas",
        argumentos={"metrica": "uti"}, motivo="preciso da UTI", resultado={"valor": 0.3},
    )  # fmt: skip

    # Pelo efeito: o evento já está no banco antes de finalizar (sobrevive a uma queda).
    motivo = conn_etl.execute(
        "SELECT motivo FROM auditoria.eventos WHERE id_execucao = %s", (id_execucao,)
    ).fetchone()
    assert motivo == ("preciso da UTI",)

    _finalizar(a)
    status, custo = conn_etl.execute(
        "SELECT status, custo_usd FROM auditoria.execucoes WHERE id_execucao = %s", (id_execucao,)
    ).fetchone()
    assert status == "ok" and float(custo) == pytest.approx(0.001)


# --- Encerramento ----------------------------------------------------------------------------


def test_encerramento_grava_total_casos_e_consumo(auditoria, conn_etl):
    _finalizar(auditoria, total_casos=1234, tokens_entrada=321, tokens_saida=45)
    linha = conn_etl.execute(
        "SELECT status, fim IS NOT NULL, data_referencia, total_casos, tokens_entrada, "
        "tokens_saida FROM auditoria.execucoes WHERE id_execucao = %s",
        (auditoria.id_execucao,),
    ).fetchone()
    assert linha == ("ok", True, date(2025, 6, 25), 1234, 321, 45)


def test_jsonl_espelha_o_banco(auditoria, conn_etl, tmp_path):
    auditoria.registrar("orquestrador", "llm", tokens_entrada=7, tokens_saida=3)
    auditoria.registrar("ferramentas", "ferramenta", motivo="conferir", resultado={"v": 1})
    _finalizar(auditoria, total_casos=99)
    linhas = [
        json.loads(t)
        for t in auditoria.gravar_jsonl(tmp_path / "auditoria.jsonl").read_text().splitlines()
    ]
    inicio, fim, total = conn_etl.execute(
        "SELECT inicio, fim, total_casos FROM auditoria.execucoes WHERE id_execucao = %s",
        (auditoria.id_execucao,),
    ).fetchone()
    # Os horários do JSONL são os do servidor (os mesmos que ficaram no banco).
    assert linhas[0]["inicio"] == inicio.isoformat() and linhas[0]["fim"] == fim.isoformat()
    assert linhas[0]["total_casos"] == total == 99
    eventos = conn_etl.execute(
        "SELECT sequencia, no, motivo, momento FROM auditoria.eventos "
        "WHERE id_execucao = %s ORDER BY sequencia",
        (auditoria.id_execucao,),
    ).fetchall()
    assert [(e["sequencia"], e["no"], e["motivo"], e["momento"]) for e in linhas[1:]] == [
        (s, n, m, t.isoformat()) for s, n, m, t in eventos
    ]


def test_registro_depois_de_finalizar_nao_chega_ao_banco(auditoria, conn_etl):
    auditoria.registrar("orquestrador", "llm")
    _finalizar(auditoria)
    with pytest.raises(RuntimeError, match="encerrada"):
        auditoria.registrar("orquestrador", "llm")
    with pytest.raises(RuntimeError, match="encerrada"):
        _finalizar(auditoria, status="erro")
    assert conn_etl.execute(
        "SELECT count(*), max(x.status) FROM auditoria.eventos "
        "JOIN auditoria.execucoes AS x USING (id_execucao) WHERE id_execucao = %s",
        (auditoria.id_execucao,),
    ).fetchone() == (1, "ok")


def test_segunda_instancia_nao_reabre_execucao_encerrada(auditoria, conn_auditor):
    # Mesmo contornando a trava em Python (outro objeto, mesmo id), o banco recusa.
    _finalizar(auditoria)
    outra = AuditoriaPostgres(conn_auditor)
    outra.id_execucao = auditoria.id_execucao
    outra.execucao = {"id_execucao": str(auditoria.id_execucao), "status": "em_andamento"}
    with pytest.raises(errors.IntegrityConstraintViolation, match="encerrada"):
        _finalizar(outra, status="erro")
    with pytest.raises(errors.IntegrityConstraintViolation, match="encerrada"):
        outra.registrar("orquestrador", "llm")


def test_finalizar_execucao_que_nao_existe_no_banco_falha(conn_auditor):
    a = AuditoriaPostgres(conn_auditor)
    a.id_execucao = uuid4()
    a.execucao = {"id_execucao": str(a.id_execucao), "status": "em_andamento"}
    with pytest.raises(LookupError):
        _finalizar(a)


def test_conexao_sem_autocommit_e_recusada(cfg_teste, banco_carregado):
    # Sem autocommit, o evento ficaria numa transação aberta e morreria com a conexão.
    with (
        conectar(cfg_teste, "auditor") as conn,
        pytest.raises(ValueError, match="autocommit"),
    ):
        AuditoriaPostgres(conn)


def test_resultado_com_nan_chega_ao_banco(auditoria, conn_etl):
    auditoria.registrar("ferramentas", "ferramenta", resultado={"taxa": float("nan")})
    assert conn_etl.execute(
        "SELECT resultado FROM auditoria.eventos WHERE id_execucao = %s",
        (auditoria.id_execucao,),
    ).fetchone() == ({"taxa": "nan"},)


def test_repr_nao_expoe_senha(cfg_teste, auditoria):
    senha = cfg_teste._senha("auditor")
    assert senha not in repr(auditoria)
    assert "password" not in repr(auditoria)


# --- Ataques do próprio auditor (SQL direto, sem passar pelo registro) -----------------------

INSERE_EXECUCAO = (
    "INSERT INTO auditoria.execucoes (id_execucao, provedor, modelo, parametros, versao_codigo, "
    "hash_prompts, {coluna}) VALUES (gen_random_uuid(), 'forjado-teste', 'm', '{{}}', 'v', 'h', "
    "{valor})"
)


@pytest.fixture
def limpar_forjadas(conn_etl):
    """Se um ataque voltar a passar, a linha forjada não fica no banco de teste."""
    yield
    conn_etl.rollback()
    conn_etl.execute("DELETE FROM auditoria.execucoes WHERE provedor = 'forjado-teste'")
    conn_etl.commit()


@pytest.mark.parametrize(
    ("coluna", "valor"),
    [
        ("status", "'ok'"),  # execução que já nasce "ok", sem nunca ter rodado
        ("fim", "now()"),
        ("inicio", "'2020-01-01'"),
        ("data_referencia", "'2025-06-25'"),
        ("total_casos", "1"),
        ("tokens_entrada", "0"),
        ("tokens_saida", "0"),
        ("custo_usd", "0"),
    ],
)
def test_auditor_nao_forja_execucao_ja_encerrada(conn_auditor, limpar_forjadas, coluna, valor):
    comando = INSERE_EXECUCAO.format(coluna=coluna, valor=valor)  # da lista fixa acima
    with pytest.raises(errors.InsufficientPrivilege):
        conn_auditor.execute(comando)


@pytest.mark.parametrize(("coluna", "valor"), [("momento", "'2020-01-01'"), ("id", "1")])
def test_auditor_nao_forja_horario_nem_id_de_evento(conn_auditor, ids_criados, coluna, valor):
    id_execucao = _execucao_aberta(conn_auditor, ids_criados)
    with pytest.raises(errors.InsufficientPrivilege):
        conn_auditor.execute(
            f"INSERT INTO auditoria.eventos (id_execucao, sequencia, no, tipo, {coluna}) "
            f"VALUES (%s, 1, 'x', 'llm', {valor})",  # da lista fixa acima
            (id_execucao,),
        )


def test_auditor_nao_acrescenta_evento_a_execucao_encerrada(auditoria, conn_auditor):
    _finalizar(auditoria)
    with pytest.raises(errors.IntegrityConstraintViolation, match="encerrada"):
        conn_auditor.execute(
            "INSERT INTO auditoria.eventos (id_execucao, sequencia, no, tipo) "
            "VALUES (%s, 99, 'x', 'llm')",
            (auditoria.id_execucao,),
        )


def test_encerrar_exige_status_final(conn_auditor, ids_criados):
    # Sem isto, "encerrar" com em_andamento deixaria a execução reescrevível para sempre.
    id_execucao = _execucao_aberta(conn_auditor, ids_criados)
    with pytest.raises(errors.IntegrityConstraintViolation, match="status final"):
        conn_auditor.execute(
            "UPDATE auditoria.execucoes SET tokens_entrada = 1 WHERE id_execucao = %s",
            (id_execucao,),
        )


def test_status_inventado_e_recusado(conn_auditor, ids_criados):
    id_execucao = _execucao_aberta(conn_auditor, ids_criados)
    with pytest.raises(errors.CheckViolation):
        conn_auditor.execute(
            "UPDATE auditoria.execucoes SET status = 'aprovado_pelo_ministerio' "
            "WHERE id_execucao = %s",
            (id_execucao,),
        )


def test_fim_vem_do_relogio_do_servidor(conn_auditor, ids_criados):
    id_execucao = _execucao_aberta(conn_auditor, ids_criados)
    (fim_forjado_ficou,) = conn_auditor.execute(
        "UPDATE auditoria.execucoes SET status = 'ok', fim = '2000-01-01' "
        "WHERE id_execucao = %s RETURNING fim < inicio",
        (id_execucao,),
    ).fetchone()
    assert fim_forjado_ficou is False


def test_evento_concorrente_ao_encerramento_e_recusado(cfg_teste, conn_auditor, ids_criados):
    """Dois cliques: um encerra (ainda sem confirmar) enquanto outro grava um evento.

    O evento tem de esperar o encerramento e, confirmado este, ser recusado. Sem a trava de
    linha no gatilho, o evento veria o status antigo e entraria depois do fim.
    """
    id_execucao = _execucao_aberta(conn_auditor, ids_criados)
    resultado: dict[str, BaseException | None] = {}
    with (
        conectar(cfg_teste, "auditor") as encerra,
        conectar(cfg_teste, "auditor", autocommit=True) as grava,
    ):
        encerra.execute(
            "UPDATE auditoria.execucoes SET status = 'ok' WHERE id_execucao = %s", (id_execucao,)
        )
        pid = grava.info.backend_pid

        def gravar_evento():
            try:
                grava.execute(
                    "INSERT INTO auditoria.eventos (id_execucao, sequencia, no, tipo) "
                    "VALUES (%s, 1, 'x', 'llm')",
                    (id_execucao,),
                )
                resultado["erro"] = None
            except psycopg.Error as erro:
                resultado["erro"] = erro

        t = threading.Thread(target=gravar_evento)
        t.start()
        esperando = _esperando_trava(conn_auditor, pid)
        encerra.commit()
        t.join(timeout=10)
    assert esperando, "o evento não esperou o encerramento em curso"
    assert isinstance(resultado["erro"], errors.IntegrityConstraintViolation)


def test_fim_e_o_relogio_do_encerramento_nao_o_inicio_da_transacao(cfg_teste, ids_criados):
    # Numa transação só, now() é o início dela: o fim ficaria igual ao início.
    id_execucao = uuid4()
    ids_criados.append(id_execucao)
    with conectar(cfg_teste, "auditor") as conn:
        conn.execute(
            "INSERT INTO auditoria.execucoes (id_execucao, provedor, modelo, parametros, "
            "versao_codigo, hash_prompts) VALUES (%s, 'teste', 'm', '{}', 'abc', 'h')",
            (id_execucao,),
        )
        conn.execute("SELECT pg_sleep(0.05)")
        (fim_depois_do_inicio,) = conn.execute(
            "UPDATE auditoria.execucoes SET status = 'ok' WHERE id_execucao = %s "
            "RETURNING fim > inicio",
            (id_execucao,),
        ).fetchone()
        conn.commit()
    assert fim_depois_do_inicio is True


# --- Regras que valem até para o dono da tabela (ETL) ----------------------------------------


@pytest.mark.parametrize(("status", "fim"), [("ok", "NULL"), ("em_andamento", "now()")])
def test_dono_da_tabela_nao_grava_fim_incoerente(conn_etl, limpar_forjadas, status, fim):
    # O auditor nem consegue gravar `fim` no INSERT, e no UPDATE o gatilho impõe o fim. Quem
    # escapa dos dois é o dono da tabela: é contra ele que execucoes_fim_coerente protege.
    with pytest.raises(errors.CheckViolation, match="execucoes_fim_coerente"):
        conn_etl.execute(
            "INSERT INTO auditoria.execucoes (id_execucao, status, fim, provedor, modelo, "
            f"parametros, versao_codigo, hash_prompts) VALUES (gen_random_uuid(), %s, {fim}, "
            "'forjado-teste', 'm', '{}', 'v', 'h')",  # `fim` vem da lista fixa acima
            (status,),
        )


def test_status_do_registro_e_o_mesmo_do_banco(conn_etl):
    # Status novo no código sem o CHECK (ou o contrário) quebraria o encerramento em produção.
    (definicao,) = conn_etl.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'auditoria.execucoes'::regclass AND conname = 'execucoes_status_valido'"
    ).fetchone()
    assert set(re.findall(r"'([^']*)'", definicao)) == STATUS_FINAIS | {"em_andamento"}


# --- Carga concorrente com o registro --------------------------------------------------------

GATILHO_DOS_EVENTOS = "CREATE OR REPLACE TRIGGER evento_so_em_execucao_aberta"


@pytest.mark.parametrize(
    "banco_antigo",
    [
        None,  # carga de rotina: tudo já existe
        # Migração: a carga precisa de ALTER TABLE (ACCESS EXCLUSIVE) em execucoes.
        "ALTER TABLE auditoria.execucoes DROP COLUMN total_casos",
        "ALTER TABLE auditoria.execucoes DROP CONSTRAINT execucoes_fim_coerente",
    ],
    ids=["rotina", "sem_total_casos", "sem_regra_do_fim"],
)
def test_carga_concorrente_ao_registro_espera_sem_deadlock(
    cfg_teste, conn_etl, ids_criados, banco_antigo
):
    """Carga (04) e registro ao mesmo tempo: um espera o outro e nenhum é derrubado.

    O registro trava eventos → execucoes (o INSERT, depois o gatilho e a FK). A carga tem de
    travar na mesma ordem; na inversa, o Postgres derruba um dos dois (DeadlockDetected). A
    carga para no meio do 04, com o registro já esperando, para o teste não depender de sorte.
    """
    if banco_antigo:
        conn_etl.execute(banco_antigo)  # da lista fixa acima
        conn_etl.commit()
    try:
        _carga_no_meio_do_registro(cfg_teste, ids_criados)
    finally:
        transformar(conn_etl, ("04_auditoria.sql",))  # volta o esquema, mesmo se falhar
        conn_etl.commit()


def _carga_no_meio_do_registro(cfg_teste, ids_criados) -> None:
    sql = ler_sql("04_auditoria.sql")
    corte = sql.index(GATILHO_DOS_EVENTOS)
    erros: list[BaseException] = []
    parar = threading.Event()
    with (
        conectar(cfg_teste, "auditor", autocommit=True) as conn_registro,
        conectar(cfg_teste, "etl") as carga,
        conectar(cfg_teste, "etl", autocommit=True) as observador,
    ):
        a = AuditoriaPostgres(conn_registro)
        ids_criados.append(
            a.iniciar_execucao(provedor="teste", modelo="m", parametros={},
                               versao_codigo="abc", hash_prompts="h")
        )  # fmt: skip
        pid = conn_registro.info.backend_pid
        # Se travar de outro jeito, falha em vez de pendurar.
        carga.execute("SET lock_timeout = '10s'")
        travar_transformacao(carga)
        carga.execute(sql[:corte])

        def registrar_em_loop():
            try:
                while not parar.is_set():
                    a.registrar("ferramentas", "ferramenta")
            except psycopg.Error as erro:
                erros.append(erro)

        t = threading.Thread(target=registrar_em_loop)
        t.start()
        esperando = _esperando_trava(observador, pid)
        try:
            carga.execute(sql[corte:])
            carga.commit()
        except psycopg.Error as erro:
            erros.append(erro)
            carga.rollback()
        finally:
            time.sleep(0.2)  # o registro volta a gravar depois da carga
            parar.set()
            t.join(timeout=15)
    assert esperando, "o registro não chegou a esperar a carga: o teste não provou a concorrência"
    assert not t.is_alive()
    assert erros == []
    assert len(a.eventos) > 0


def test_carga_nao_bloqueia_a_leitura_da_auditoria(cfg_teste, banco_carregado, conn_auditor):
    # Trava bloqueia a ação, nunca a visão: durante a carga, a trilha continua legível.
    with conectar(cfg_teste, "etl") as carga:
        carga.execute("SELECT 1")  # abre a transação: o transformar vira savepoint e não confirma
        transformar(carga, ("04_auditoria.sql",))  # travas da carga seguras até o rollback
        conn_auditor.execute("SET lock_timeout = '1s'")
        try:
            for tabela in ("auditoria.execucoes", "auditoria.eventos"):
                conn_auditor.execute(f"SELECT count(*) FROM {tabela}")  # nome da lista fixa
        finally:
            conn_auditor.execute("RESET lock_timeout")
            carga.rollback()


# --- Migração: banco de versão anterior e reaplicação ----------------------------------------


def test_reaplicar_tira_insert_amplo_de_versao_anterior(conn_etl, conn_auditor, limpar_forjadas):
    # Versão anterior dava INSERT em todas as colunas: o 04 reaplicado volta ao INSERT por coluna.
    conn_etl.execute("GRANT INSERT ON auditoria.execucoes, auditoria.eventos TO srag_auditor")
    conn_etl.commit()
    transformar(conn_etl, ("04_auditoria.sql",))
    conn_etl.commit()
    with pytest.raises(errors.InsufficientPrivilege):
        conn_auditor.execute(INSERE_EXECUCAO.format(coluna="status", valor="'ok'"))


def test_reaplicar_e_idempotente(conn_etl):
    transformar(conn_etl, ("04_auditoria.sql",))
    transformar(conn_etl, ("04_auditoria.sql",))
    conn_etl.commit()
    contagem = conn_etl.execute(
        "SELECT (SELECT count(*) FROM pg_constraint"
        "        WHERE conrelid = 'auditoria.execucoes'::regclass AND contype = 'c'),"
        "       (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN"
        "        ('auditoria.execucoes'::regclass, 'auditoria.eventos'::regclass))"
    ).fetchone()
    assert contagem == (2, 2)


def test_execucao_legada_com_status_fora_da_regra_nao_trava_a_carga(conn_etl):
    # Linha gravada antes da regra (status livre): a trilha não se reescreve, e a carga segue.
    conn_etl.execute(
        "ALTER TABLE auditoria.execucoes DROP CONSTRAINT IF EXISTS execucoes_status_valido, "
        "DROP CONSTRAINT IF EXISTS execucoes_fim_coerente"
    )
    (id_legado,) = conn_etl.execute(
        "INSERT INTO auditoria.execucoes (id_execucao, status, provedor, modelo, parametros, "
        "versao_codigo, hash_prompts) VALUES (gen_random_uuid(), 'legado', 'p', 'm', '{}', "
        "'v', 'h') RETURNING id_execucao"
    ).fetchone()
    conn_etl.commit()
    try:
        transformar(conn_etl, ("04_auditoria.sql",))
        conn_etl.commit()
        assert conn_etl.execute(
            "SELECT status FROM auditoria.execucoes WHERE id_execucao = %s", (id_legado,)
        ).fetchone() == ("legado",)
        # A regra vale para o que se grava daqui em diante (até para o dono da tabela).
        with pytest.raises(errors.CheckViolation):
            conn_etl.execute(
                "INSERT INTO auditoria.execucoes (id_execucao, status, provedor, modelo, "
                "parametros, versao_codigo, hash_prompts) VALUES (gen_random_uuid(), "
                "'legado', 'p', 'm', '{}', 'v', 'h')"
            )
    finally:
        conn_etl.rollback()
        conn_etl.execute("DELETE FROM auditoria.execucoes WHERE id_execucao = %s", (id_legado,))
        for restricao in RESTRICOES:
            conn_etl.execute(f"ALTER TABLE auditoria.execucoes VALIDATE CONSTRAINT {restricao}")
        conn_etl.commit()


RESTRICOES = ("execucoes_status_valido", "execucoes_fim_coerente")


def test_banco_sem_linha_legada_nasce_com_a_regra_validada(conn_etl):
    conn_etl.execute(
        "ALTER TABLE auditoria.execucoes DROP CONSTRAINT IF EXISTS execucoes_status_valido, "
        "DROP CONSTRAINT IF EXISTS execucoes_fim_coerente"
    )
    conn_etl.commit()
    transformar(conn_etl, ("04_auditoria.sql",))
    conn_etl.commit()
    assert conn_etl.execute(
        "SELECT conname, convalidated FROM pg_constraint "
        "WHERE conrelid = 'auditoria.execucoes'::regclass AND conname = ANY(%s) ORDER BY 1",
        (list(RESTRICOES),),
    ).fetchall() == [("execucoes_fim_coerente", True), ("execucoes_status_valido", True)]


def test_regra_nao_validada_e_validada_quando_o_historico_fica_limpo(conn_etl):
    # Carga com linha legada deixa a regra NOT VALID; limpa a trilha, a carga seguinte valida.
    conn_etl.execute(
        "ALTER TABLE auditoria.execucoes DROP CONSTRAINT IF EXISTS execucoes_status_valido, "
        "DROP CONSTRAINT IF EXISTS execucoes_fim_coerente"
    )
    (id_legado,) = conn_etl.execute(
        "INSERT INTO auditoria.execucoes (id_execucao, status, provedor, modelo, parametros, "
        "versao_codigo, hash_prompts) VALUES (gen_random_uuid(), 'legado', 'p', 'm', '{}', "
        "'v', 'h') RETURNING id_execucao"
    ).fetchone()
    conn_etl.commit()
    validadas = (
        "SELECT conname, convalidated FROM pg_constraint "
        "WHERE conrelid = 'auditoria.execucoes'::regclass AND conname = ANY(%s) ORDER BY 1"
    )
    try:
        transformar(conn_etl, ("04_auditoria.sql",))
        conn_etl.commit()
        assert [v for _, v in conn_etl.execute(validadas, (list(RESTRICOES),))] == [False, False]
        conn_etl.execute("DELETE FROM auditoria.execucoes WHERE id_execucao = %s", (id_legado,))
        conn_etl.commit()
        transformar(conn_etl, ("04_auditoria.sql",))
        conn_etl.commit()
        assert [v for _, v in conn_etl.execute(validadas, (list(RESTRICOES),))] == [True, True]
    finally:
        conn_etl.rollback()
        conn_etl.execute("DELETE FROM auditoria.execucoes WHERE id_execucao = %s", (id_legado,))
        for restricao in RESTRICOES:
            conn_etl.execute(f"ALTER TABLE auditoria.execucoes VALIDATE CONSTRAINT {restricao}")
        conn_etl.commit()


def test_banco_sem_total_casos_ganha_a_coluna(conn_etl):
    # Banco criado antes da coluna: a carga acrescenta, e o auditor pode gravá-la no encerramento.
    conn_etl.execute("ALTER TABLE auditoria.execucoes DROP COLUMN total_casos")
    conn_etl.commit()
    transformar(conn_etl, ("04_auditoria.sql",))
    conn_etl.commit()
    assert conn_etl.execute(
        "SELECT has_column_privilege('srag_auditor', 'auditoria.execucoes', 'total_casos', "
        "'UPDATE')"
    ).fetchone() == (True,)
