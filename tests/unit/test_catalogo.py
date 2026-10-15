from datetime import date

import pytest

from srag_agente.metricas.catalogo import CATALOGO, calcular_metrica
from srag_agente.metricas.repositorio import Referencia


class FonteFalsa:
    def __init__(self, indicadores: dict, janelas: dict):
        self._indicadores = indicadores
        self._janelas = janelas

    def referencia(self):
        return Referencia(date(2025, 6, 25), date(2025, 6, 11), date(2025, 1, 1), 1000)

    def indicadores(self, uf):
        return self._indicadores

    def janelas_aumento(self, uf):
        return self._janelas


INDICADORES = {
    "total_casos": 1000,
    "obitos": 80,
    "com_desfecho": 800,
    "uti_sim": 290,
    "uti_informada": 900,
    "vacina_covid_sim": 600,
    "vacina_covid_informada": 800,
    "vacina_gripe_sim": 250,
    "vacina_gripe_informada": 780,
    "periodo_inicio": date(2025, 1, 1),
    "periodo_fim": date(2025, 6, 25),
}
JANELAS = {
    "casos_7d_atual": 84,
    "casos_7d_anterior": 70,
    "casos_30d_atual": 300,
    "casos_30d_anterior": 250,
    "casos_em_consolidacao": 150,
    "data_consolidada": date(2025, 6, 11),
}


def test_todas_as_metricas_tem_definicao_e_limitacao():
    assert set(CATALOGO) == {"aumento_casos", "mortalidade", "uti", "vacinacao"}
    assert all(m.definicao and m.limitacao for m in CATALOGO.values())


def test_mortalidade_usa_casos_com_desfecho():
    r = calcular_metrica("mortalidade", FonteFalsa(INDICADORES, JANELAS))
    assert r.valor == pytest.approx(0.10)
    assert (r.numerador, r.denominador, r.excluidos) == (80, 800, 200)


def test_aumento_compara_semanas_consolidadas():
    r = calcular_metrica("aumento_casos", FonteFalsa(INDICADORES, JANELAS))
    assert r.valor == pytest.approx(0.20)
    assert r.periodo_fim == date(2025, 6, 11)
    assert r.periodo_inicio == date(2025, 5, 29)
    assert r.excluidos == 150
    assert r.complemento["variacao_30d"] == pytest.approx(0.20)


def test_vacinacao_traz_gripe_como_complemento():
    r = calcular_metrica("vacinacao", FonteFalsa(INDICADORES, JANELAS))
    assert r.valor == pytest.approx(0.75)
    assert r.complemento["vacina_gripe"] == pytest.approx(250 / 780)


def test_contagem_pequena_e_suprimida():
    pequenos = {**INDICADORES, "obitos": 2, "com_desfecho": 30}
    r = calcular_metrica("mortalidade", FonteFalsa(pequenos, JANELAS))
    assert r.suprimido is True
    assert r.valor is None
    assert r.numerador == "<5"


def test_complemento_pequeno_tambem_e_suprimido():
    pequenos = {**INDICADORES, "vacina_gripe_sim": 3}
    r = calcular_metrica("vacinacao", FonteFalsa(pequenos, JANELAS))
    assert r.complemento["vacina_gripe_numerador"] == "<5"
    assert r.complemento["vacina_gripe"] is None


def test_denominador_zero_da_valor_nulo():
    vazio = {**INDICADORES, "uti_sim": 0, "uti_informada": 0}
    assert calcular_metrica("uti", FonteFalsa(vazio, JANELAS)).valor is None
