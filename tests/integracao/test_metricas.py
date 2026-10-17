from datetime import timedelta

import pytest

from srag_agente.metricas.catalogo import calcular_metrica
from srag_agente.metricas.repositorio import TEMPO_MAXIMO, DadosIndisponiveis, RepositorioGold
from tests.dados import oraculo
from tests.dados.amostra import REFERENCIA

pytestmark = pytest.mark.integracao


@pytest.fixture
def repo(conn_agente):
    return RepositorioGold(conn_agente)


@pytest.fixture
def casos(banco_carregado):
    return oraculo.limpar(banco_carregado)


@pytest.mark.parametrize("uf", ["BR", "SP", "PI"])
@pytest.mark.parametrize("chave", ["mortalidade", "uti", "vacinacao"])
def test_razoes_batem_com_o_oraculo(repo, casos, chave, uf):
    esperado = getattr(oraculo, chave)(casos, uf)
    assert calcular_metrica(chave, repo, uf).valor == pytest.approx(esperado)


def test_aumento_brasil_e_vinte_por_cento(repo):
    # Amostra: 12 casos/dia de 05/06 a 11/06 contra 10 casos/dia de 29/05 a 04/06.
    r = calcular_metrica("aumento_casos", repo)
    assert (r.numerador, r.denominador) == (84, 70)
    assert r.valor == pytest.approx(0.20)


@pytest.mark.parametrize("uf", ["SP", "RJ"])
def test_aumento_por_uf_bate_com_o_oraculo(repo, casos, uf):
    consolidada = REFERENCIA - timedelta(days=14)
    atual = oraculo.casos_entre(casos, uf, consolidada - timedelta(days=6), consolidada)
    anterior = oraculo.casos_entre(
        casos, uf, consolidada - timedelta(days=13), consolidada - timedelta(days=7)
    )
    r = calcular_metrica("aumento_casos", repo, uf)
    assert (r.numerador, r.denominador) == (atual, anterior)


def _esperado_suprimido(*contagens):
    return any(0 < c < 5 for c in contagens)


@pytest.mark.parametrize("uf", ["BR", "SP", "PI"])
def test_excluidos_e_complemento_da_vacinacao_batem_com_o_oraculo(repo, casos, uf):
    total, _, _, _, _, covid_sim, covid_inf, gripe_sim, gripe_inf, *_ = oraculo.indicadores(casos)[
        uf
    ]
    r = calcular_metrica("vacinacao", repo, uf)
    assert r.excluidos == (
        total - covid_inf if total - covid_inf >= 5 or total == covid_inf else "<5"
    )
    if r.suprimido:
        assert _esperado_suprimido(covid_sim, covid_inf, covid_inf - covid_sim)
        return
    assert r.complemento["vacina_gripe_denominador"] == gripe_inf
    if r.complemento["vacina_gripe"] is not None:
        assert r.complemento["vacina_gripe_numerador"] == gripe_sim
        assert r.complemento["vacina_gripe"] == pytest.approx(gripe_sim / gripe_inf)


@pytest.mark.parametrize("uf", ["BR", "SP", "RJ"])
def test_complemento_e_excluidos_do_aumento_batem_com_o_oraculo(repo, casos, uf):
    atual7, ant7, atual30, ant30, em_consolidacao = oraculo.janelas(casos)[uf]
    r = calcular_metrica("aumento_casos", repo, uf)
    assert r.excluidos == (em_consolidacao if em_consolidacao >= 5 or not em_consolidacao else "<5")
    assert (r.numerador, r.denominador) == (atual7, ant7)
    assert r.complemento["casos_30d_atual"] == atual30
    assert r.complemento["casos_30d_anterior"] == ant30
    assert r.complemento["variacao_30d"] == pytest.approx(atual30 / ant30 - 1)


@pytest.mark.parametrize("uf", ["BR", "SP", "PI"])
@pytest.mark.parametrize("chave", ["mortalidade", "uti", "vacinacao"])
def test_excluidos_das_razoes_batem_com_o_oraculo(repo, casos, chave, uf):
    total, _, desfecho, _, uti_i, _, cov_i, *_ = oraculo.indicadores(casos)[uf]
    denominador = {"mortalidade": desfecho, "uti": uti_i, "vacinacao": cov_i}[chave]
    r = calcular_metrica(chave, repo, uf)
    esperado = total - denominador
    assert r.excluidos == (esperado if esperado == 0 or esperado >= 5 else "<5")


def test_uf_sem_casos_levanta_dados_indisponiveis(repo):
    with pytest.raises(DadosIndisponiveis):
        calcular_metrica("mortalidade", repo, "RR")


def test_janelas_de_uf_ausente_levanta_dados_indisponiveis(repo):
    with pytest.raises(DadosIndisponiveis):
        repo.janelas_aumento("RR")
    with pytest.raises(DadosIndisponiveis):
        repo.indicadores("RR")


_SONDA = (
    "SELECT current_setting('transaction_read_only') AS ro, "
    "current_setting('statement_timeout') AS t"
)


def test_consulta_roda_em_transacao_somente_leitura_com_timeout(conn_agente):
    # Adultera a sessão antes: o que vale é o que o repositório impõe, não o default do papel.
    conn_agente.execute("SET default_transaction_read_only = off")
    conn_agente.execute("SET statement_timeout = 0")
    linha = RepositorioGold(conn_agente)._linhas(_SONDA)[0]
    assert linha == {"ro": "on", "t": TEMPO_MAXIMO}


def test_sessao_nao_fica_alterada_depois_da_consulta(conn_agente):
    conn_agente.execute("SET statement_timeout = '7s'")
    RepositorioGold(conn_agente).referencia()
    # SET LOCAL morre com a transação: continua o valor da sessão, não o do repositório.
    assert conn_agente.execute("SHOW statement_timeout").fetchone()[0] == "7s"
    assert conn_agente.info.transaction_status.name == "IDLE"


def test_series_e_distribuicao_tem_o_formato_esperado(repo):
    diaria = repo.serie_diaria("BR")
    assert len(diaria) == 30 and set(diaria[0]) == {"dia", "casos", "em_consolidacao"}
    mensal = repo.serie_mensal("BR")
    assert len(mensal) == 12 and set(mensal[0]) == {"mes", "casos", "fora_da_base", "parcial"}
    distribuicao = repo.distribuicao("classificacao", "BR")
    assert distribuicao and all(set(d) == {"categoria", "casos"} for d in distribuicao)
