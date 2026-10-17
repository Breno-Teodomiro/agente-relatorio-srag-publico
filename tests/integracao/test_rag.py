import pytest

from srag_agente.ferramentas.dicionario import BuscaDicionario
from srag_agente.rag import indexar
from tests.fakes import embedder_falso

pytestmark = pytest.mark.integracao

PAGINAS = [
    "Evolução do caso 1-Cura 2-Óbito 3-Óbito por outras causas 9-Ignorado\nEVOLUCAO\n"
    "Internado em UTI? 1-Sim 2-Não 9-Ignorado. O paciente foi internado em UTI?\nUTI\n"
]


def test_indexa_e_busca_o_campo_certo(conn_etl, conn_agente, monkeypatch):
    monkeypatch.setattr(indexar, "extrair_paginas", lambda pdf: PAGINAS)
    total = indexar.indexar_dicionario(
        conn_etl,
        pdf=None,
        embedder=embedder_falso,
        campos_conhecidos=indexar.campos_da_raw(conn_etl),
    )
    assert total == 2

    texto_uti = conn_etl.execute(
        "SELECT texto FROM gold.dicionario_trechos WHERE campo = 'UTI'"
    ).fetchone()[0]
    resultado = BuscaDicionario(conn_agente, embedder_falso, k=1).buscar(texto_uti)
    assert resultado[0].campo == "UTI"
    assert resultado[0].pagina == 1
    assert resultado[0].distancia == pytest.approx(0, abs=1e-6)
