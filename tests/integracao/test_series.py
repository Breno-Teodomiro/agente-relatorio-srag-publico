from collections import Counter
from datetime import timedelta

import pytest

from srag_agente.metricas.repositorio import RepositorioGold
from srag_agente.metricas.series import calcular_distribuicao, calcular_serie
from srag_agente.metricas.supressao import suprimir
from tests.dados import oraculo
from tests.dados.amostra import REFERENCIA

pytestmark = pytest.mark.integracao


@pytest.fixture
def repo(conn_agente):
    return RepositorioGold(conn_agente)


@pytest.fixture
def casos(banco_carregado):
    return oraculo.limpar(banco_carregado)


def test_serie_diaria_bate_com_o_oraculo(repo, casos):
    serie = calcular_serie("diaria_30d", repo)
    por_dia = Counter(c["dt_sintomas"] for c in casos)
    esperado = [suprimir(por_dia[REFERENCIA - timedelta(days=29 - i)]) for i in range(30)]
    assert [p.casos for p in serie.pontos] == esperado
    assert [p.em_consolidacao for p in serie.pontos] == [False] * 16 + [True] * 14


def test_serie_mensal_marca_meses_fora_da_base_e_parciais(repo, casos):
    serie = calcular_serie("mensal_12m", repo)
    rotulos = [p.rotulo for p in serie.pontos]
    assert rotulos[0] == "2024-07" and rotulos[-1] == "2025-06"
    fora = [p for p in serie.pontos if p.fora_da_base]
    assert [p.rotulo for p in fora] == [
        "2024-07",
        "2024-08",
        "2024-09",
        "2024-10",
        "2024-11",
        "2024-12",
    ]
    assert all(p.casos is None for p in fora)
    assert serie.pontos[-1].parcial is True
    por_mes = Counter(c["dt_sintomas"].strftime("%Y-%m") for c in casos)
    assert {p.rotulo: p.casos for p in serie.pontos if not p.fora_da_base} == {
        mes: suprimir(por_mes[mes]) for mes in rotulos[6:]
    }


def test_distribuicao_soma_o_total(repo, casos):
    d = calcular_distribuicao("classificacao", repo)
    assert {c.categoria: c.casos for c in d.categorias} == {
        categoria: suprimir(n) for categoria, n in oraculo.contar(casos, "classificacao").items()
    }
    assert d.total == suprimir(len(casos))
    assert sum(c.percentual or 0 for c in d.categorias) <= 1 + 1e-9
