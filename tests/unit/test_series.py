import random
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


def test_serie_diaria_oculta_o_menor_dia_do_bloco_do_suprimido():
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
    # O dia 3 (28/05) está na semana anterior do aumento e em maio. O menor dia exibido da série
    # (o 7 da posição 10) é de outra semana: soma da semana - barras devolveria o 2. O oculto
    # sai do mesmo bloco (29 a 31/05: semana anterior e maio): o primeiro 20 dele.
    assert valores[2] == ROTULO_OCULTO
    assert valores[10] == 7 and valores[25] == 7
    assert serie.total == 20 * 27 + 2 + 7 + 7
    assert [v for v in valores if isinstance(v, str)] == [ROTULO_OCULTO, "<5"]
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


# --- Supressão entre a série diária e os agregados exatos da mesma página (I1 da Task 14) ---
# A página publica, além das barras, somas exatas de pedaços da janela de 30 dias: a semana atual
# e a anterior do aumento (numerador e denominador), os dias em consolidação ("Fora do cálculo")
# e o mês corrente (rótulo do gráfico mensal). Soma da janela - barras visíveis da janela não pode
# isolar uma célula escondida.


class FonteJanelas:
    """Série diária arbitrária; janelas e meses calculados como a gold calcula (03_gold.sql)."""

    def __init__(self, casos: list[int], referencia: date = REF):
        assert len(casos) == 30
        self.casos = casos
        self.ref = referencia
        self.consolidada = referencia - timedelta(days=14)
        self.dias = [referencia - timedelta(days=29 - i) for i in range(30)]

    def referencia(self):
        return Referencia(self.ref, self.consolidada, date(2024, 1, 1), 10_000)

    def serie_diaria(self, uf):
        return [
            {"dia": d, "casos": c, "em_consolidacao": d > self.consolidada}
            for d, c in zip(self.dias, self.casos, strict=True)
        ]

    def soma_entre(self, inicio: date, fim: date) -> int:
        return sum(c for d, c in zip(self.dias, self.casos, strict=True) if inicio <= d <= fim)

    def janelas_aumento(self, uf):
        c = self.consolidada
        return {
            "casos_7d_atual": self.soma_entre(c - timedelta(days=6), c),
            "casos_7d_anterior": self.soma_entre(c - timedelta(days=13), c - timedelta(days=7)),
            "casos_30d_atual": 500,
            "casos_30d_anterior": 500,
            "casos_em_consolidacao": self.soma_entre(c + timedelta(days=1), self.ref),
            "data_consolidada": c,
        }

    def serie_mensal(self, uf):
        mes = self.ref.strftime("%Y-%m")
        return [
            {"mes": "2025-01", "casos": 900, "fora_da_base": False, "parcial": False},
            {"mes": mes, "casos": self.soma_entre(self.ref.replace(day=1), self.ref),
             "fora_da_base": False, "parcial": True},
        ]  # fmt: skip

    # Janelas publicadas com soma exata, em posições da série (calculadas aqui, não pelo módulo).
    def janelas_publicadas(self) -> dict[str, set[int]]:
        c, d = self.consolidada, self.dias
        return {
            "7d_atual": {i for i in range(30) if c - timedelta(days=6) <= d[i] <= c},
            "7d_anterior": {
                i for i in range(30) if c - timedelta(days=13) <= d[i] <= c - timedelta(days=7)
            },
            "em_consolidacao": {i for i in range(30) if d[i] > c},
            "mes_corrente": {i for i in range(30) if d[i] >= self.ref.replace(day=1)},
            # Mês fechado inteiro na janela (fevereiro, com referência em 1º/03): barra exata.
            "mes_fechado": {
                i
                for i in range(30)
                if d[i] < self.ref.replace(day=1) and d[i].replace(day=1) >= d[0]
            },
            "total": set(range(30)),
        }


def _isola_escondida(fonte: FonteJanelas, valores: list) -> list[str]:
    """Janelas em que soma exata - barras visíveis cai numa única célula escondida."""
    reveladas = []
    for nome, janela in fonte.janelas_publicadas().items():
        escondidas = [i for i in janela if isinstance(valores[i], str)]
        if len(escondidas) == 1:
            reveladas.append(nome)
    return reveladas


def test_cenario_da_revisao_denominador_menos_barras_nao_revela_o_suprimido():
    from srag_agente.metricas.catalogo import calcular_metrica

    casos = [10] * 30
    casos[5] = 3  # dia na semana anterior do aumento
    fonte = FonteJanelas(casos)
    valores = [p.casos for p in calcular_serie("diaria_30d", fonte).pontos]
    janela = fonte.janelas_publicadas()["7d_anterior"]
    assert 5 in janela and valores[5] == "<5"
    denominador = calcular_metrica("aumento_casos", fonte).denominador
    assert denominador == 63  # exato na página
    visiveis = sum(valores[i] for i in janela if isinstance(valores[i], int))
    # Antes: visíveis = 60 e 63 - 60 = 3. Agora a diferença cobre ao menos duas células.
    assert len([i for i in janela if isinstance(valores[i], str)]) >= 2
    assert denominador - visiveis >= 3 + 5
    assert _isola_escondida(fonte, valores) == []


def test_rotulo_do_mes_corrente_menos_barras_nao_revela_o_suprimido():
    casos = [10] * 30
    casos[20] = 2  # em consolidação e dentro do mês corrente
    fonte = FonteJanelas(casos)
    valores = [p.casos for p in calcular_serie("diaria_30d", fonte).pontos]
    assert valores[20] == "<5"
    assert _isola_escondida(fonte, valores) == []


def test_fora_do_calculo_menos_barras_nao_revela_o_suprimido():
    casos = [10] * 30
    casos[29] = 4
    fonte = FonteJanelas(casos)
    valores = [p.casos for p in calcular_serie("diaria_30d", fonte).pontos]
    escondidas = {i for i, v in enumerate(valores) if isinstance(v, str)}
    assert escondidas & fonte.janelas_publicadas()["em_consolidacao"] == escondidas
    assert _isola_escondida(fonte, valores) == []


def test_propriedade_nenhuma_janela_publicada_isola_celula_escondida():
    """Série aleatória e data de referência em todos os dias do mês (muda o corte do mês)."""
    from tests.unit.test_supressao import _determinadas_por_bloco

    rnd = random.Random(20261017)
    esgotadas = 0
    for rodada in range(3000):
        casos = [
            rnd.choice([0, 0, rnd.randint(1, 4), rnd.randint(1, 4), rnd.randint(5, 40)])
            for _ in range(30)
        ]
        fonte = FonteJanelas(casos, date(2025, 1 + rodada % 12, 1) + timedelta(days=rodada % 31))
        valores = [p.casos for p in calcular_serie("diaria_30d", fonte).pontos]
        janelas = list(fonte.janelas_publicadas().values())
        determinadas, esgotou = _determinadas_por_bloco(casos, valores, janelas)
        assert determinadas == 0, (fonte.ref, casos)
        if esgotou:  # bloco sem nenhuma célula >= 5 para esconder: limite documentado
            esgotadas += 1
            continue
        for nome, janela in fonte.janelas_publicadas().items():
            escondidas = [i for i in janela if isinstance(valores[i], str)]
            if len(escondidas) == 1:  # só aceitável se a escondida não é "<5" sozinha
                assert valores[escondidas[0]] != "<5", (nome, fonte.ref, casos)
    assert esgotadas < 3000


def test_mes_inteiro_na_serie_diaria_nao_serve_de_complemento_na_mensal():
    # Junho (1 a 25) está inteiro na janela diária: se fosse o "oculto" da série mensal, a soma
    # das barras diárias o devolveria, e total - exibidos revelaria o mês "<5".
    class Fonte(FonteFalsa):
        def serie_mensal(self, uf):
            return [
                {"mes": "2025-03", "casos": 3, "fora_da_base": False, "parcial": True},
                {"mes": "2025-04", "casos": 40, "fora_da_base": False, "parcial": False},
                {"mes": "2025-05", "casos": 30, "fora_da_base": False, "parcial": False},
                {"mes": "2025-06", "casos": 8, "fora_da_base": False, "parcial": True},
            ]

    serie = calcular_serie("mensal_12m", Fonte())
    assert [p.casos for p in serie.pontos] == ["<5", 40, ROTULO_OCULTO, 8]
    assert serie.total == 81
