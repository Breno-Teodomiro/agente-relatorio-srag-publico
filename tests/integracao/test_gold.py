"""A gold confere com o oráculo, sobrevive às recargas e mostra só o que o contrato prevê."""

from collections import Counter

import pytest

from srag_agente.banco import conectar
from srag_agente.dados.transformar import transformar
from tests.dados import oraculo
from tests.dados.amostra import REFERENCIA

pytestmark = pytest.mark.integracao

# Contrato da gold: tudo que o agente enxerga. Lista permitida, não proibida: relação ou coluna
# nova na gold só entra mudando este teste (e o spec).
CONTRATO_GOLD = {
    "referencia": ("v", ["data_referencia", "data_consolidada", "inicio_base", "total_casos"]),
    "casos_diarios": ("v", ["uf", "dia", "casos"]),
    "indicadores": ("v", [
        "uf", "total_casos", "obitos", "com_desfecho", "uti_sim", "uti_informada",
        "vacina_covid_sim", "vacina_covid_informada", "vacina_gripe_sim", "vacina_gripe_informada",
        "periodo_inicio", "periodo_fim",
    ]),
    "janelas_aumento": ("v", [
        "uf", "casos_7d_atual", "casos_7d_anterior", "casos_30d_atual", "casos_30d_anterior",
        "casos_em_consolidacao", "data_consolidada",
    ]),
    "distribuicao": ("v", ["dimensao", "uf", "categoria", "casos"]),
    "dicionario_trechos": ("r", ["id", "campo", "pagina", "texto", "embedding"]),
}  # fmt: skip
VIEWS_GOLD = [nome for nome, (tipo, _) in CONTRATO_GOLD.items() if tipo == "v"]


def _casos(linhas):
    return oraculo.limpar(linhas)


def test_referencia_e_a_data_maxima_de_sintomas(conn_agente, banco_carregado):
    data_referencia, data_consolidada, inicio, total = conn_agente.execute(
        "SELECT data_referencia, data_consolidada, inicio_base, total_casos FROM gold.referencia"
    ).fetchone()
    assert data_referencia == REFERENCIA
    assert (data_referencia - data_consolidada).days == 14
    assert inicio == min(c["dt_sintomas"] for c in _casos(banco_carregado) if c["dt_sintomas"])
    assert total == len(banco_carregado)


def test_total_brasil_soma_as_ufs(conn_agente):
    br, soma = conn_agente.execute(
        "SELECT sum(total_casos) FILTER (WHERE uf = 'BR'), "
        "sum(total_casos) FILTER (WHERE uf <> 'BR') FROM gold.indicadores"
    ).fetchone()
    assert br == soma


def test_uf_invalida_aparece_como_nd(conn_agente):
    nd = conn_agente.execute("SELECT total_casos FROM gold.indicadores WHERE uf = 'ND'").fetchone()
    assert nd == (1,)


def test_indicadores_batem_com_o_oraculo(conn_agente, banco_carregado):
    linhas = conn_agente.execute(
        "SELECT uf, total_casos, obitos, com_desfecho, uti_sim, uti_informada, vacina_covid_sim, "
        "vacina_covid_informada, vacina_gripe_sim, vacina_gripe_informada, periodo_inicio, "
        "periodo_fim FROM gold.indicadores"
    ).fetchall()
    assert {uf: tuple(resto) for uf, *resto in linhas} == oraculo.indicadores(
        _casos(banco_carregado)
    )


def test_janelas_batem_com_o_oraculo(conn_agente, banco_carregado):
    linhas = conn_agente.execute(
        "SELECT uf, casos_7d_atual, casos_7d_anterior, casos_30d_atual, casos_30d_anterior, "
        "casos_em_consolidacao FROM gold.janelas_aumento"
    ).fetchall()
    assert {uf: tuple(resto) for uf, *resto in linhas} == oraculo.janelas(_casos(banco_carregado))


def test_janelas_do_brasil_conferidas_a_mao(conn_agente):
    # A amostra tem 10 casos/dia de 29/05 a 04/06 e 12 casos/dia de 05/06 a 11/06.
    # Consolidado até 11/06 (25/06 menos 14 dias); de 12/06 a 25/06: 3 casos nos dias ímpares
    # e 4 nos pares, 7 de cada → 49 em consolidação.
    assert conn_agente.execute(
        "SELECT casos_7d_atual, casos_7d_anterior, casos_em_consolidacao, data_consolidada "
        "FROM gold.janelas_aumento WHERE uf = 'BR'"
    ).fetchone() == (84, 70, 49, REFERENCIA.replace(day=11))


def test_casos_diarios_do_brasil_somam_as_ufs_dia_a_dia(conn_agente, banco_carregado):
    diferentes = conn_agente.execute(
        "SELECT dia FROM gold.casos_diarios GROUP BY dia "
        "HAVING sum(casos) FILTER (WHERE uf = 'BR') <> sum(casos) FILTER (WHERE uf <> 'BR')"
    ).fetchall()
    assert diferentes == []
    total_br = conn_agente.execute(
        "SELECT sum(casos) FROM gold.casos_diarios WHERE uf = 'BR'"
    ).fetchone()[0]
    assert total_br == sum(1 for c in _casos(banco_carregado) if c["dt_sintomas"])


def test_distribuicao_bate_com_o_oraculo(conn_agente, banco_carregado):
    linhas = conn_agente.execute(
        "SELECT dimensao, uf, categoria, casos FROM gold.distribuicao"
    ).fetchall()
    assert Counter({(d, uf, cat): n for d, uf, cat, n in linhas}) == oraculo.distribuicao(
        _casos(banco_carregado)
    )


def test_gold_tem_so_o_que_o_contrato_preve(conn_etl):
    relacoes = conn_etl.execute(
        "SELECT c.relname, c.relkind::text, "
        "array_agg(a.attname::text ORDER BY a.attnum) "
        "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
        "JOIN pg_attribute AS a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped "
        "WHERE n.nspname = 'gold' AND c.relkind IN ('r', 'v', 'm', 'p', 'f') "
        "GROUP BY c.relname, c.relkind"
    ).fetchall()
    assert {nome: (tipo, colunas) for nome, tipo, colunas in relacoes} == CONTRATO_GOLD


def _oids_gold(conn) -> dict[str, int | None]:
    return {
        view: conn.execute("SELECT to_regclass(%s)::oid", (f"gold.{view}",)).fetchone()[0]
        for view in VIEWS_GOLD
    }


def test_transformar_reconstroi_a_gold_junto_com_a_silver(cfg_teste, banco_carregado):
    # A silver é recriada com DROP ... CASCADE, que derruba as views da gold. Depois do
    # transformar completo, elas têm de existir de novo (OID novo) e o agente tem de lê-las.
    with conectar(cfg_teste, "etl", autocommit=True) as conn:
        antes = _oids_gold(conn)
        transformar(conn)
        depois = _oids_gold(conn)
    assert all(depois.values())
    assert all(depois[v] != antes[v] for v in VIEWS_GOLD)
    with conectar(cfg_teste, "agente", autocommit=True) as conn:
        total = conn.execute("SELECT total_casos FROM gold.referencia").fetchone()[0]
    assert total == len(banco_carregado)


@pytest.mark.parametrize(
    "arquivos",
    [
        ("01_silver.sql",),
        ("01_silver.sql", "02_qualidade.sql"),
        ("03_gold.sql", "01_silver.sql"),  # gold antes da silver: o CASCADE a apagaria de novo
        ("01_silver.sql", "02_qualidade.sql", "04_auditoria.sql"),
    ],
)
def test_transformar_recusa_silver_sem_recriar_a_gold(cfg_teste, banco_carregado, arquivos):
    with conectar(cfg_teste, "etl", autocommit=True) as conn:
        antes = _oids_gold(conn)
        oid_silver = conn.execute("SELECT 'silver.casos'::regclass::oid").fetchone()[0]
        with pytest.raises(ValueError, match=r"03_gold\.sql"):
            transformar(conn, arquivos)
        assert _oids_gold(conn) == antes
        assert conn.execute("SELECT 'silver.casos'::regclass::oid").fetchone()[0] == oid_silver


def test_base_vazia_nao_quebra_a_gold(cfg_teste, banco_carregado):
    # Raw sem nenhuma linha: a gold continua consultável, com zero casos e sem datas.
    with conectar(cfg_teste, "etl") as conn:
        try:
            conn.execute("DELETE FROM raw.srag")
            transformar(conn)
            assert conn.execute("SELECT * FROM gold.referencia").fetchall() == [
                (None, None, None, 0)
            ]
            assert conn.execute(
                "SELECT uf, total_casos, obitos, com_desfecho FROM gold.indicadores"
            ).fetchall() == [("BR", 0, 0, 0)]
            for view in ("casos_diarios", "janelas_aumento", "distribuicao"):
                # nome vem da lista fixa acima
                assert conn.execute(f"SELECT count(*) FROM gold.{view}").fetchone()[0] == 0
        finally:
            conn.rollback()
