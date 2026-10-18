"""Percentual: meio para cima em decimal exato, igual no cartão e na instrução ao redator."""

import pytest

from srag_agente.arredondamento import percentual, pontos
from srag_agente.guardrails.saida import _pct
from srag_agente.relatorio.formatacao import pct, variacao


@pytest.mark.parametrize(
    ("fracao", "esperado"),
    [
        (0.3125, "31,3"),  # empate exato: o `format` do Python daria 31,2 ("para o par")
        (0.2856, "28,6"),
        (0.0125, "1,3"),
        (0.0135, "1,4"),
        (0.285, "28,5"),  # em binário 0.285 * 100 = 28.499999999999996
        (0.1, "10,0"),
        (0.0, "0,0"),
        (1.0, "100,0"),
    ],
)
def test_percentual_arredonda_meio_para_cima(fracao, esperado):
    assert percentual(fracao) == esperado


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(31.25, "31,3"), (28.56, "28,6"), (27.95, "28,0"), (0.05, "0,1"), (100.0, "100,0")],
)
def test_pontos_recebe_o_valor_ja_em_pontos_percentuais(valor, esperado):
    assert pontos(valor) == esperado


def test_percentual_com_outras_casas():
    assert percentual(0.3125, 0) == "31"
    assert percentual(0.3125, 2) == "31,25"


def test_cartao_e_instrucao_ao_redator_mostram_o_mesmo_numero():
    # caso real do primeiro uso online: 126 contra 96 dá +31,25%
    fracao = 126 / 96 - 1
    assert variacao(fracao) == "+31,3%"
    assert pct(fracao) == "31,3%"
    assert _pct(fracao * 100) == "31,3%"  # o guardrail recebe pontos percentuais, não fração


def test_formatadores_continuam_tratando_nulo_e_sinal():
    assert pct(None) == "—"
    assert variacao(None) == "—"
    assert variacao(-0.2731).startswith("−")  # noqa: RUF001 (menos tipográfico)
    assert variacao(0.0) == "0,0%"
