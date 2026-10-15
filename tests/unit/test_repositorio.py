import pytest
from psycopg.errors import QueryCanceled

from srag_agente.metricas.repositorio import TENTATIVAS, DadosIndisponiveis, RepositorioGold


class ConexaoFalsa:
    autocommit = True


def repositorio_com(respostas):
    """Repositório cuja execução devolve (ou levanta) cada resposta, em ordem."""
    repo = RepositorioGold(ConexaoFalsa())
    chamadas = []

    def tentar(consulta, parametros):
        chamadas.append(consulta)
        resposta = respostas[len(chamadas) - 1]
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    repo._tentar = tentar
    return repo, chamadas


def test_cancelamento_transitorio_e_repetido():
    repo, chamadas = repositorio_com([QueryCanceled(), [{"uf": "BR"}]])
    assert repo.indicadores("BR") == {"uf": "BR"}
    assert len(chamadas) == 2


def test_cancelamento_persistente_estoura_depois_das_tentativas():
    repo, chamadas = repositorio_com([QueryCanceled()] * TENTATIVAS)
    with pytest.raises(QueryCanceled):
        repo.indicadores("BR")
    assert len(chamadas) == TENTATIVAS


def test_linha_ausente_vira_dados_indisponiveis():
    for metodo in ("indicadores", "janelas_aumento"):
        repo, _ = repositorio_com([[]])
        with pytest.raises(DadosIndisponiveis):
            getattr(repo, metodo)("RR")


def test_exige_autocommit():
    class SemAutocommit:
        autocommit = False

    with pytest.raises(ValueError):
        RepositorioGold(SemAutocommit())
