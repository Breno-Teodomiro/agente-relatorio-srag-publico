from datetime import date, timedelta

import pytest

from srag_agente.metricas.repositorio import DadosIndisponiveis, Referencia
from srag_agente.metricas.series import calcular_distribuicao, calcular_serie

REF = date(2025, 6, 25)


class FonteFalsa:
    def __init__(self, casos_por_dia: int = 10, distribuicao: list[dict] | None = None):
        self._casos = casos_por_dia
        self._distribuicao = (
            distribuicao
            if distribuicao is not None
            else [
                {"categoria": "influenza", "casos": 60},
                {"categoria": "covid19", "casos": 3},
            ]
        )

    def referencia(self):
        return Referencia(REF, REF - timedelta(days=14), date(2025, 1, 1), 1000)

    def serie_diaria(self, uf):
        return [
            {"dia": REF - timedelta(days=29 - i), "casos": self._casos, "em_consolidacao": i >= 16}
            for i in range(30)
        ]

    def serie_mensal(self, uf):
        return [{"mes": "2025-06", "casos": self._casos, "fora_da_base": False, "parcial": True}]

    def distribuicao(self, dimensao, uf):
        return self._distribuicao


def test_serie_diaria_tem_30_pontos_e_marca_consolidacao():
    serie = calcular_serie("diaria_30d", FonteFalsa())
    assert len(serie.pontos) == 30
    assert sum(p.em_consolidacao for p in serie.pontos) == 14
    assert serie.pontos[-1].rotulo == REF.isoformat()
    assert serie.total == 300


def test_serie_suprime_contagens_pequenas():
    serie = calcular_serie("diaria_30d", FonteFalsa(casos_por_dia=2))
    assert {p.casos for p in serie.pontos} == {"<5"}


def test_distribuicao_calcula_percentual_e_suprime_pequenas():
    d = calcular_distribuicao("classificacao", FonteFalsa())
    influenza, covid = d.categorias
    assert influenza.percentual == pytest.approx(60 / 63)
    assert covid.casos == "<5"
    assert covid.percentual is None


def test_distribuicao_vazia_levanta_dados_indisponiveis():
    with pytest.raises(DadosIndisponiveis):
        calcular_distribuicao("classificacao", FonteFalsa(distribuicao=[]))


def test_serie_mensal_suprime_e_nao_quebra_com_mes_fora_da_base():
    class Fonte(FonteFalsa):
        def serie_mensal(self, uf):
            return [
                {"mes": "2024-12", "casos": None, "fora_da_base": True, "parcial": False},
                {"mes": "2025-01", "casos": 3, "fora_da_base": False, "parcial": True},
            ]

    serie = calcular_serie("mensal_12m", Fonte())
    assert [p.casos for p in serie.pontos] == [None, "<5"]
    assert serie.pontos[0].fora_da_base is True
    assert serie.total == "<5"
