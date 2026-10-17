from collections import Counter
from datetime import timedelta

import pytest

from srag_agente.metricas.repositorio import RepositorioGold
from srag_agente.metricas.series import calcular_distribuicao, calcular_serie
from srag_agente.metricas.supressao import ROTULO_OCULTO, suprimir, suprimir_celulas
from tests.dados import oraculo
from tests.dados.amostra import INICIO, REFERENCIA

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
    exatos = [por_dia[REFERENCIA - timedelta(days=29 - i)] for i in range(30)]
    assert [p.casos for p in serie.pontos] == suprimir_celulas(exatos)
    # Na amostra, dias de 3 e 4 casos viram "<5"; o menor dia exibido (o 1º com 10 casos,
    # 29/05) some como "oculto" para que total - exibidos não devolva os suprimidos.
    ocultos = [p.rotulo for p in serie.pontos if p.casos == ROTULO_OCULTO]
    assert ocultos == ["2025-05-29"]
    assert serie.total == sum(exatos)
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
    # O 1º mês da base só é parcial se a base não começa no dia 1. Na amostra começa em 01/01.
    assert repo.referencia().inicio_base == INICIO
    assert [p.parcial for p in serie.pontos] == [False] * 11 + [True]
    por_mes = Counter(c["dt_sintomas"].strftime("%Y-%m") for c in casos)
    exatos = [por_mes[mes] for mes in rotulos[6:]]
    assert [p.casos for p in serie.pontos[6:]] == suprimir_celulas(exatos)


def _esperado_distribuicao(casos, dimensao):
    contagem = oraculo.contar(casos, dimensao)
    ordem = sorted(contagem, key=lambda categoria: (-contagem[categoria], categoria))
    return dict(zip(ordem, suprimir_celulas([contagem[c] for c in ordem]), strict=True)), contagem


def test_distribuicao_soma_o_total(repo, casos):
    d = calcular_distribuicao("classificacao", repo)
    esperado, _ = _esperado_distribuicao(casos, "classificacao")
    assert {c.categoria: c.casos for c in d.categorias} == esperado
    assert d.total == suprimir(len(casos))
    assert sum(c.percentual or 0 for c in d.categorias) <= 1 + 1e-9


def test_distribuicao_suprimida_oculta_complemento_e_percentual_bate(repo, casos):
    d = calcular_distribuicao("faixa_etaria", repo)
    esperado, contagem = _esperado_distribuicao(casos, "faixa_etaria")
    assert contagem["ignorado"] == 1  # idade 150 na amostra
    por_categoria = {c.categoria: c for c in d.categorias}
    assert {k: c.casos for k, c in por_categoria.items()} == esperado
    assert por_categoria["ignorado"].casos == "<5"
    ocultas = [c for c in d.categorias if c.casos == ROTULO_OCULTO]
    assert len(ocultas) == 1
    assert contagem[ocultas[0].categoria] == min(n for n in contagem.values() if n >= 5)
    assert d.total == len(casos)
    for c in d.categorias:
        if isinstance(c.casos, int):
            assert c.percentual == pytest.approx(contagem[c.categoria] / len(casos))
        else:
            assert c.percentual is None
