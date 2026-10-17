from collections import Counter
from datetime import timedelta

import pytest

from srag_agente.metricas.catalogo import calcular_metrica
from srag_agente.metricas.repositorio import RepositorioGold
from srag_agente.metricas.series import calcular_distribuicao, calcular_serie, janelas_publicadas
from srag_agente.metricas.supressao import ROTULO_OCULTO, suprimir, suprimir_celulas
from tests.dados import oraculo
from tests.dados.amostra import INICIO, REFERENCIA
from tests.unit.test_supressao import _determinadas_por_bloco

UFS = ["BR", "SP", "RJ", "MG", "PI"]

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
    assert [p.casos for p in serie.pontos] == [suprimir(c) for c in exatos]
    # Na amostra, dias de 3 e 4 casos viram "<5". Eles ficam em 27-28/05 (bloco de 2 dias, soma 7:
    # 3+4 ou 4+3) e nos 14 dias em consolidação (soma 49): cada bloco se protege sozinho, e
    # esconder um dia de 10 em outra semana não protegeria nada (a soma da semana é publicada).
    assert [p.rotulo for p in serie.pontos if p.casos == ROTULO_OCULTO] == []
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


def _diarios(casos, uf):
    recorte = oraculo.do_recorte(casos, uf)
    por_dia = Counter(c["dt_sintomas"] for c in recorte)
    dias = [REFERENCIA - timedelta(days=29 - i) for i in range(30)]
    return dias, [por_dia[d] for d in dias]


@pytest.mark.parametrize("uf", UFS)
def test_janelas_da_serie_somam_o_mesmo_que_a_gold(repo, casos, uf):
    """A supressão protege as janelas que o módulo declara: elas têm de ser as publicadas."""
    dias, exatos = _diarios(casos, uf)
    janelas = janelas_publicadas(dias, repo.referencia().data_consolidada)

    def soma(nome):
        return sum(exatos[i] for i in janelas[nome])

    gold = repo.janelas_aumento(uf)
    assert soma("aumento_7d_atual") == gold["casos_7d_atual"]
    assert soma("aumento_7d_anterior") == gold["casos_7d_anterior"]
    assert soma("em_consolidacao") == gold["casos_em_consolidacao"]
    # O mês corrente cabe inteiro na série (referência 25/06): a soma é a barra do mês.
    assert set(janelas) == {"aumento_7d_atual", "aumento_7d_anterior", "em_consolidacao",
                            "mes_2025-06"}  # fmt: skip
    mensal = repo.serie_mensal(uf)
    assert soma("mes_2025-06") == next(m["casos"] for m in mensal if m["mes"] == "2025-06")


@pytest.mark.parametrize("uf", UFS)
def test_nenhum_agregado_exato_da_pagina_isola_dia_escondido(repo, casos, uf):
    """O passo da revisão (I1): agregado exato da página - barras visíveis da mesma janela."""
    dias, exatos = _diarios(casos, uf)
    valores = [p.casos for p in calcular_serie("diaria_30d", repo, uf).pontos]
    aumento = calcular_metrica("aumento_casos", repo, uf)
    mes = calcular_serie("mensal_12m", repo, uf).pontos[-1]
    consolidada = repo.referencia().data_consolidada
    publicados = {
        "numerador": (aumento.numerador, consolidada - timedelta(days=6), consolidada),
        "denominador": (
            aumento.denominador,
            consolidada - timedelta(days=13),
            consolidada - timedelta(days=7),
        ),
        "fora_do_calculo": (aumento.excluidos, consolidada + timedelta(days=1), REFERENCIA),
        "rotulo_mes_corrente": (mes.casos, REFERENCIA.replace(day=1), REFERENCIA),
    }
    janelas = []
    for nome, (valor, inicio, fim) in publicados.items():
        janela = {i for i, d in enumerate(dias) if inicio <= d <= fim}
        janelas.append(janela)
        if not isinstance(valor, int):
            continue  # agregado já mascarado na página: não fecha conta nenhuma
        escondidas = [i for i in janela if isinstance(valores[i], str)]
        visiveis = sum(valores[i] for i in janela if isinstance(valores[i], int))
        assert valor == sum(exatos[i] for i in janela)
        if len(escondidas) == 1:
            assert valores[escondidas[0]] != "<5", (uf, nome, valor - visiveis)
    determinadas, _ = _determinadas_por_bloco(exatos, valores, janelas)
    assert determinadas == 0, uf
