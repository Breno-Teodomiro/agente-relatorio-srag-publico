import pytest

from srag_agente.banco import conectar
from srag_agente.ferramentas.dicionario import BuscaDicionario
from srag_agente.rag import indexar
from srag_agente.rag.embeddings import DIMENSAO, criar_embedder
from tests.fakes import embedder_falso

pytestmark = pytest.mark.integracao

LINHAS = [
    (1, ["Evolução do caso", "Varchar2(1)", "1-Cura 2-Óbito 9-Ignorado", "", "", "EVOLUCAO"]),
    (
        1,
        [
            "Internado em UTI?",
            "Varchar2(1)",
            "1-Sim 2-Não 9-Ignorado",
            "O paciente foi internado em UTI?",
            "",
            "UTI",
        ],
    ),
    (
        2,
        [
            "Recebeu vacina COVID-19?",
            "Varchar(1)",
            "1-Sim 2-Não 9-Ignorado",
            "Conforme o cartão.",
            "",
            "VACINA COV _",
        ],
    ),
    (
        2,
        [
            "Classificação final do caso",
            "Varchar2(1)",
            "1-SRAG por influenza",
            "",
            "",
            "CLASSI FIN _",
        ],
    ),
    (2, ["Outra saída", "", "", "Primeira ocorrência do campo repetido.", "", "SOR OUT _"]),
    (2, ["Outra saída", "", "", "Segunda ocorrência do campo repetido.", "", "SOR OUT _"]),
]
CAMPOS = {"EVOLUCAO", "UTI", "VACINA_COV", "CLASSI_FIN", "SOR_OUT"}


@pytest.fixture
def conn_indexador(cfg_teste, banco_carregado):
    """Autocommit: `conn.transaction()` do indexador é a transação de fato e confirma ao sair."""
    with conectar(cfg_teste, "etl", autocommit=True) as conn:
        yield conn


@pytest.fixture
def paginas_falsas(monkeypatch):
    monkeypatch.setattr(indexar, "extrair_linhas", lambda pdf: LINHAS)


def indexar_amostra(conn):
    return indexar.indexar_dicionario(conn, None, embedder_falso, CAMPOS)


def contar(conn) -> int:
    return conn.execute("SELECT count(*) FROM gold.dicionario_trechos").fetchone()[0]


def test_indexa_e_busca_o_campo_certo(conn_indexador, conn_agente, paginas_falsas):
    total = indexar_amostra(conn_indexador)
    assert total == 6

    texto_uti = conn_indexador.execute(
        "SELECT texto FROM gold.dicionario_trechos WHERE campo = 'UTI'"
    ).fetchone()[0]
    resultado = BuscaDicionario(conn_agente, embedder_falso, k=1).buscar(texto_uti)
    assert resultado[0].campo == "UTI"
    assert resultado[0].pagina == 1
    assert resultado[0].distancia == pytest.approx(0, abs=1e-6)


def test_reindexar_substitui_e_nao_acumula(conn_indexador, paginas_falsas):
    primeiro = indexar_amostra(conn_indexador)
    segundo = indexar_amostra(conn_indexador)
    assert primeiro == segundo == contar(conn_indexador)


def test_sem_campos_conhecidos_falha_e_preserva_o_indice(conn_indexador, paginas_falsas):
    indexar_amostra(conn_indexador)
    antes = contar(conn_indexador)
    with pytest.raises(ValueError, match="sem campos conhecidos"):
        indexar.indexar_dicionario(conn_indexador, None, embedder_falso, set())
    assert contar(conn_indexador) == antes > 0


def test_falta_de_campo_das_metricas_falha_e_preserva_o_indice(conn_indexador, paginas_falsas):
    indexar_amostra(conn_indexador)
    antes = contar(conn_indexador)
    with pytest.raises(ValueError, match="CLASSI_FIN"):
        indexar.indexar_dicionario(conn_indexador, None, embedder_falso, CAMPOS - {"CLASSI_FIN"})
    assert contar(conn_indexador) == antes > 0


def test_conexao_sem_autocommit_e_recusada(conn_etl, paginas_falsas):
    with pytest.raises(ValueError, match="autocommit"):
        indexar_amostra(conn_etl)


def test_nome_citado_na_pergunta_vem_primeiro_sem_repetir(
    conn_indexador, conn_agente, paginas_falsas
):
    indexar_amostra(conn_indexador)
    resultado = BuscaDicionario(conn_agente, embedder_falso, k=3).buscar(
        "O que significa o campo VACINA_COV?"
    )
    assert resultado[0].campo == "VACINA_COV"
    assert len(resultado) == 3
    assert len({(t.campo, t.texto) for t in resultado}) == 3


def test_campo_duplicado_no_dicionario_devolve_as_duas_ocorrencias(
    conn_indexador, conn_agente, paginas_falsas
):
    indexar_amostra(conn_indexador)
    resultado = BuscaDicionario(conn_agente, embedder_falso, k=3).buscar("SOR_OUT")
    assert [t.campo for t in resultado[:2]] == ["SOR_OUT", "SOR_OUT"]
    assert resultado[0].texto != resultado[1].texto


def test_pergunta_sem_nome_de_campo_usa_so_a_similaridade(
    conn_indexador, conn_agente, paginas_falsas
):
    indexar_amostra(conn_indexador)
    resultado = BuscaDicionario(conn_agente, embedder_falso, k=2).buscar("pergunta em minúsculas")
    assert len(resultado) == 2


def test_dimensao_do_embedder_real_bate_com_a_da_tabela_e_a_do_falso(cfg_teste):
    vetor = criar_embedder(cfg_teste.pasta_cache_modelos)(["teste"])[0]
    assert len(vetor) == DIMENSAO == len(embedder_falso(["teste"])[0])
