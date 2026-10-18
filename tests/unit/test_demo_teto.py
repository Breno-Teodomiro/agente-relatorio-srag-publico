"""Teto diário da demonstração: decisão pura (`motivo_teto`), leitura do uso e memória.

Nada aqui fala com banco: a conexão é um dublê que devolve a linha da consulta.
"""

import math
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from srag_agente.demo import SQLSTATE_TETO, teto
from srag_agente.guardrails.limites import Limites

RENOVA = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)  # meia-noite de Brasília em UTC
RESERVA = 0.1
BUSCAS = 4


def _uso(**campos) -> teto.UsoDiario:
    base = teto.UsoDiario(
        relatorios=0, abertas=0, custo_usd=0.0, buscas=0,
        relatorios_dia=10, buscas_dia=40, custo_usd_dia=1.0, renova_em=RENOVA,
    )  # fmt: skip
    return replace(base, **campos)


def _motivo(uso: teto.UsoDiario) -> str | None:
    return teto.motivo_teto(uso, RESERVA, BUSCAS)


# --- relatórios: atingir o limite já bloqueia ---


@pytest.mark.parametrize(("feitos", "bloqueia"), [(9, False), (10, True), (11, True)])
def test_relatorios_na_fronteira(feitos, bloqueia):
    motivo = _motivo(_uso(relatorios=feitos))
    assert (motivo is not None) is bloqueia
    if bloqueia:
        assert "10 relatórios" in motivo


# --- buscas: a próxima execução pode fazer até `buscas_por_execucao` ---


@pytest.mark.parametrize(("feitas", "bloqueia"), [(35, False), (36, False), (37, True)])
def test_buscas_na_fronteira(feitas, bloqueia):
    # 36 + 4 = 40 (igual ao limite: cabe); 37 + 4 = 41 (passa)
    motivo = _motivo(_uso(buscas=feitas))
    assert (motivo is not None) is bloqueia
    if bloqueia:
        assert "buscas" in motivo


# --- gasto: o já gasto + reserva por execução aberta + a que vai começar ---


@pytest.mark.parametrize(
    ("gasto", "bloqueia"),
    [(0.89, False), (0.9, False), (0.9000001, True)],  # 0,9 + 0,1 = 1,0: igual cabe
)
def test_gasto_na_fronteira(gasto, bloqueia):
    motivo = _motivo(_uso(custo_usd=gasto))
    assert (motivo is not None) is bloqueia
    if bloqueia:
        assert "gasto" in motivo


def test_reserva_conta_as_execucoes_abertas_mais_a_proxima():
    # sem abertas: 0,7 + 1 x 0,1 = 0,8 (cabe); com 3 abertas: 0,7 + 4 x 0,1 = 1,1 (passa)
    assert _motivo(_uso(custo_usd=0.7)) is None
    assert _motivo(_uso(custo_usd=0.7, abertas=2)) is None  # 0,7 + 0,3 = 1,0: igual cabe
    assert _motivo(_uso(custo_usd=0.7, abertas=3)) is not None


def test_reserva_zero_com_modelo_gratuito_so_olha_o_gasto():
    uso = _uso(custo_usd=1.0, abertas=50)
    assert teto.motivo_teto(uso, 0.0, BUSCAS) is None
    assert teto.motivo_teto(replace(uso, custo_usd=1.01), 0.0, BUSCAS) is not None


@pytest.mark.parametrize(
    "campos",
    [
        {"custo_usd": math.nan},  # numeric do Postgres aceita NaN, e NaN > x é falso
        {"custo_usd_dia": math.nan},  # CHECK (x > 0) aceita NaN no Postgres
        {"custo_usd_dia": math.inf},
        {"custo_usd": -0.5},  # gasto negativo "devolveria" crédito
    ],
)
def test_valor_estranho_vindo_do_banco_bloqueia(campos):
    assert _motivo(_uso(**campos)) is not None


def test_reserva_estranha_bloqueia():
    assert teto.motivo_teto(_uso(), math.nan, BUSCAS) is not None
    assert teto.motivo_teto(_uso(), -1.0, BUSCAS) is not None


def test_dia_vazio_libera():
    assert _motivo(_uso()) is None


def test_custo_maximo_de_uma_execucao_usa_o_preco_mais_alto():
    cfg = SimpleNamespace(max_tokens_execucao=60_000, preco_entrada_usd_mtok=0.4,
                          preco_saida_usd_mtok=1.6)  # fmt: skip
    assert teto.custo_maximo_execucao(cfg) == pytest.approx(0.096)
    cfg.preco_entrada_usd_mtok = 3.0  # entrada mais cara que a saída: vale a entrada
    assert teto.custo_maximo_execucao(cfg) == pytest.approx(0.18)


# --- leitura do uso: sem linha de limites, falha fechada ---


class _Conexao:
    """Dublê de conexão: registra o SQL; cada `fetchone` devolve a próxima linha da fila."""

    def __init__(self, *linhas):
        self.linhas = list(linhas)
        self.sql: list[str] = []

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, parametros=None):
        self.sql.append(str(sql))
        return SimpleNamespace(fetchone=lambda: self.linhas.pop(0) if self.linhas else None)


def _linha_uso():
    return (3, 1, 0.25, 8, 10, 40, 1.0, RENOVA)


def test_ler_uso_monta_o_uso_na_ordem_da_consulta():
    conn = _Conexao(_linha_uso())
    uso = teto.ler_uso(conn)
    assert uso == teto.UsoDiario(3, 1, 0.25, 8, 10, 40, 1.0, RENOVA)
    assert any("statement_timeout" in s for s in conn.sql)  # leitura não prende a trava sem fim
    assert teto.SQL_USO_HOJE in conn.sql


def test_ler_uso_sem_linha_de_limites_e_erro():
    with pytest.raises(teto.TetoIndisponivelError):
        teto.ler_uso(_Conexao(None))


def test_sql_do_uso_conta_o_dia_no_fuso_do_banco_e_todas_as_situacoes():
    sql = teto.SQL_USO_HOJE
    assert "auditoria.limites_demo" in sql and "WHERE id = 1" in sql
    assert "now() AT TIME ZONE l.fuso" in sql  # relógio do banco, não do contêiner
    # relatórios contam qualquer situação: o filtro de status só aparece na contagem de abertas
    assert sql.count("status = 'em_andamento'") == 1
    assert "ferramenta = 'buscar_noticias'" in sql
    assert "v.tipo = 'ferramenta'" in sql  # busca recusada vira evento `decisao`: não conta


def test_conferir_teto_le_com_o_auditor_e_decide(monkeypatch):
    papeis = []

    @contextmanager
    def conectar(cfg, papel, autocommit=False):
        papeis.append((papel, autocommit))
        yield _Conexao((10, 0, 0.0, 0, 10, 40, 1.0, RENOVA))

    monkeypatch.setattr(teto, "conectar", conectar)
    cfg = SimpleNamespace(max_tokens_execucao=1000, preco_entrada_usd_mtok=1.0,
                          preco_saida_usd_mtok=1.0)  # fmt: skip
    assert "10 relatórios" in teto.conferir_teto(cfg)
    assert papeis == [("auditor", True)]


def test_conferir_teto_usa_o_teto_de_buscas_do_limites(monkeypatch):
    feitas = 40 - Limites().max_buscas_noticias + 1  # uma a mais do que cabe

    @contextmanager
    def conectar(cfg, papel, autocommit=False):
        yield _Conexao((0, 0, 0.0, feitas, 10, 40, 1.0, RENOVA))

    monkeypatch.setattr(teto, "conectar", conectar)
    cfg = SimpleNamespace(max_tokens_execucao=1, preco_entrada_usd_mtok=0.0,
                          preco_saida_usd_mtok=0.0)  # fmt: skip
    assert "buscas" in teto.conferir_teto(cfg)


def test_conferir_teto_propaga_falha_de_leitura(monkeypatch):
    @contextmanager
    def conectar(cfg, papel, autocommit=False):
        raise RuntimeError("banco fora")
        yield

    monkeypatch.setattr(teto, "conectar", conectar)
    with pytest.raises(RuntimeError):
        teto.conferir_teto(SimpleNamespace())


# --- prontidão do banco da demo (conferida na partida da tela) ---


def test_prontidao_ok_com_gatilho_e_limites():
    assert teto.problemas_banco_demo(_Conexao((1,), _linha_uso())) == []


def test_prontidao_sem_gatilho_e_problema():
    problemas = teto.problemas_banco_demo(_Conexao(None, _linha_uso()))
    assert any(teto.GATILHO_TETO in p for p in problemas)


def test_prontidao_sem_limites_e_problema():
    problemas = teto.problemas_banco_demo(_Conexao((1,), None))
    assert any("limites" in p for p in problemas)


def test_nome_do_gatilho_e_sqlstate_batem_com_o_sql_da_carga():
    from pathlib import Path

    sql = Path(teto.__file__).parent / "sql" / "02_teto_demo.sql"
    if not sql.exists():
        pytest.skip("02_teto_demo.sql ainda não existe (vem com a carga da demo)")
    texto = sql.read_text(encoding="utf-8")
    assert f"TRIGGER {teto.GATILHO_TETO}" in texto
    assert f"'{SQLSTATE_TETO}'" in texto


# --- memória do processo (log da plataforma) ---


def test_memoria_le_rss_pico_e_limite_do_cgroup_v2(tmp_path):
    status = tmp_path / "status"
    status.write_text("Name:\tpython\nVmHWM:\t  204800 kB\nVmRSS:\t  102400 kB\n")
    v2 = tmp_path / "memory.max"
    v2.write_text("1073741824\n")
    m = teto.memoria_kb(status=status, cgroup_v2=v2, cgroup_v1=tmp_path / "nada")
    assert m == {"rss_kb": 102400, "pico_kb": 204800, "limite_kb": 1048576}


def test_memoria_sem_limite_ou_sem_arquivo_vira_none(tmp_path):
    v2 = tmp_path / "memory.max"
    v2.write_text("max\n")
    m = teto.memoria_kb(status=tmp_path / "nada", cgroup_v2=v2, cgroup_v1=tmp_path / "nada")
    assert m == {"rss_kb": None, "pico_kb": None, "limite_kb": None}


def test_memoria_cai_para_o_cgroup_v1(tmp_path):
    v1 = tmp_path / "limit_in_bytes"
    v1.write_text("2147483648\n")
    m = teto.memoria_kb(status=tmp_path / "nada", cgroup_v2=tmp_path / "nada", cgroup_v1=v1)
    assert m["limite_kb"] == 2097152


def test_pacote_ausente_vira_none():
    assert teto.ler_pacote(_Conexao(None)) is None
    pacote = teto.ler_pacote(_Conexao(("ab" * 32, "ded6a6e", None)))
    assert pacote == teto.PacoteDemo("ab" * 32, "ded6a6e", None)


def test_prontidao_sem_a_tabela_de_limites_diz_o_que_falta():
    import psycopg

    class SemTabela(_Conexao):
        def execute(self, sql, parametros=None):
            if "limites_demo" in str(sql):
                raise psycopg.errors.UndefinedTable("relation does not exist")
            return super().execute(sql, parametros)

    problemas = teto.problemas_banco_demo(SemTabela((1,)))
    assert problemas == ["falta a tabela auditoria.limites_demo"]
