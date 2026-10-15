from datetime import timedelta

import pytest

from srag_agente.metricas.catalogo import calcular_metrica
from srag_agente.metricas.repositorio import DadosIndisponiveis, RepositorioGold
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


def test_uf_sem_casos_levanta_dados_indisponiveis(repo):
    with pytest.raises(DadosIndisponiveis):
        calcular_metrica("mortalidade", repo, "RR")


def test_janelas_de_uf_ausente_levanta_dados_indisponiveis(repo):
    with pytest.raises(DadosIndisponiveis):
        repo.janelas_aumento("RR")
    with pytest.raises(DadosIndisponiveis):
        repo.indicadores("RR")


def test_consulta_roda_em_transacao_somente_leitura_com_timeout(conn_agente):
    repo = RepositorioGold(conn_agente)
    linha = repo._linhas(
        "SELECT current_setting('transaction_read_only') AS ro, "
        "current_setting('statement_timeout') AS t"
    )[0]
    assert linha == {"ro": "on", "t": "5s"}


def test_sessao_nao_fica_alterada_depois_da_consulta(conn_agente):
    RepositorioGold(conn_agente).referencia()
    assert conn_agente.execute("SHOW statement_timeout").fetchone()[0] == "5s"
    assert conn_agente.info.transaction_status.name == "IDLE"


def test_series_e_distribuicao_tem_o_formato_esperado(repo):
    diaria = repo.serie_diaria("BR")
    assert len(diaria) == 30 and set(diaria[0]) == {"dia", "casos", "em_consolidacao"}
    mensal = repo.serie_mensal("BR")
    assert len(mensal) == 12 and set(mensal[0]) == {"mes", "casos", "fora_da_base", "parcial"}
    assert all(set(d) == {"categoria", "casos"} for d in repo.distribuicao("sexo", "BR"))
