"""Avaliação do RAG: modos de busca e diferença entre eles. Sem banco nem modelo."""

from contextlib import contextmanager

import pytest

from srag_agente.rag import avaliar

VETOR = {"original": (11, 14, 16), "validação": (19, 27, 36)}
TEXTO = {"original": (11, 14, 16), "validação": (13, 21, 36)}


def test_diferenca_e_texto_menos_vetor_por_conjunto():
    assert avaliar.diferenca(VETOR, TEXTO) == [
        "original: 1º lugar +0, top 3 +0 (de 16)",
        "validação: 1º lugar -6, top 3 -6 (de 36)",
    ]


@pytest.fixture
def espioes(monkeypatch):
    montadas, carregados = [], []

    @contextmanager
    def conectar(cfg, papel, autocommit=False):
        yield "conn"

    monkeypatch.setattr(avaliar, "Config", lambda: type("C", (), {"pasta_cache_modelos": "p"})())
    monkeypatch.setattr(avaliar, "conectar", conectar)
    monkeypatch.setattr(avaliar, "criar_embedder", lambda pasta: carregados.append(pasta) or "m")
    monkeypatch.setattr(
        avaliar, "BuscaDicionario", lambda conn, emb, k: montadas.append(emb) or f"busca-{emb}"
    )
    monkeypatch.setattr(avaliar, "medir", lambda busca: VETOR if busca == "busca-m" else TEXTO)
    return montadas, carregados


def test_modo_padrao_e_vetor_e_carrega_o_modelo(espioes, capsys):
    montadas, carregados = espioes
    avaliar.principal([])
    assert montadas == ["m"] and carregados == ["p"]
    assert "#####" not in capsys.readouterr().out  # saída de sempre, sem cabeçalho novo


def test_modo_texto_nao_carrega_o_modelo(espioes):
    montadas, carregados = espioes
    avaliar.principal(["--modo", "texto"])
    assert montadas == [None] and carregados == []


def test_modo_ambos_imprime_os_dois_e_a_diferenca(espioes, capsys):
    montadas, carregados = espioes
    avaliar.principal(["--modo", "ambos"])
    saida = capsys.readouterr().out
    assert montadas == ["m", None] and carregados == ["p"]
    assert "Busca: vetor" in saida and "Busca: texto" in saida
    assert "validação: 1º lugar -6, top 3 -6 (de 36)" in saida


def test_modo_invalido_e_recusado(espioes):
    with pytest.raises(SystemExit):
        avaliar.principal(["--modo", "outro"])
