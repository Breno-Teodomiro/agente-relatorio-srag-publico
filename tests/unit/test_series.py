from datetime import date, timedelta

import pytest

from srag_agente.metricas.repositorio import DadosIndisponiveis, Referencia
from srag_agente.metricas.series import calcular_distribuicao, calcular_serie
from srag_agente.metricas.supressao import ROTULO_OCULTO

REF = date(2025, 6, 25)


class FonteFalsa:
    def __init__(self, casos_por_dia: int = 10, distribuicao: list[dict] | None = None):
        self._casos = casos_por_dia
        self._distribuicao = (
            distribuicao
            if distribuicao is not None
            else [
                {"categoria": "influenza", "casos": 60},
                {"categoria": "outro_virus", "casos": 20},
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
    influenza, outro, covid = d.categorias
    assert d.total == 83
    assert influenza.casos == 60
    assert influenza.percentual == pytest.approx(60 / 83)
    assert covid.casos == "<5"
    assert covid.percentual is None
    # Supressão complementar: senão 83 - 60 - 20 devolveria os 3 casos de covid19.
    assert outro.casos == ROTULO_OCULTO
    assert outro.percentual is None
    assert ROTULO_OCULTO in d.nota


def test_distribuicao_sem_pequenas_exibe_tudo():
    d = calcular_distribuicao(
        "classificacao",
        FonteFalsa(distribuicao=[{"categoria": "a", "casos": 30}, {"categoria": "b", "casos": 10}]),
    )
    assert [(c.casos, c.percentual) for c in d.categorias] == [(30, 0.75), (10, 0.25)]


def test_serie_diaria_oculta_o_menor_dia_quando_ha_um_so_suprimido():
    class Fonte(FonteFalsa):
        def serie_diaria(self, uf):
            casos = [20] * 30
            casos[3], casos[10], casos[25] = 2, 7, 7
            return [
                {"dia": REF - timedelta(days=29 - i), "casos": c, "em_consolidacao": i >= 16}
                for i, c in enumerate(casos)
            ]

    serie = calcular_serie("diaria_30d", Fonte())
    valores = [p.casos for p in serie.pontos]
    assert valores[3] == "<5"
    assert valores[10] == ROTULO_OCULTO  # empate em 7: o primeiro dia na ordem
    assert valores[25] == 7
    assert serie.total == 20 * 27 + 2 + 7 + 7
    assert [v for v in valores if isinstance(v, str)] == ["<5", ROTULO_OCULTO]
    assert ROTULO_OCULTO in serie.nota


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


def test_serie_mensal_oculta_o_menor_mes_e_preserva_fora_da_base():
    class Fonte(FonteFalsa):
        def serie_mensal(self, uf):
            return [
                {"mes": "2024-12", "casos": None, "fora_da_base": True, "parcial": False},
                {"mes": "2025-01", "casos": 3, "fora_da_base": False, "parcial": True},
                {"mes": "2025-02", "casos": 8, "fora_da_base": False, "parcial": False},
                {"mes": "2025-03", "casos": 20, "fora_da_base": False, "parcial": True},
            ]

    serie = calcular_serie("mensal_12m", Fonte())
    assert [p.casos for p in serie.pontos] == [None, "<5", ROTULO_OCULTO, 20]
    assert serie.total == 31
    assert ROTULO_OCULTO in serie.nota
