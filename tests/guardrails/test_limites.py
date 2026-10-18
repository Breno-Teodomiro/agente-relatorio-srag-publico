"""Limites de execução: chamadas de ferramenta, tokens e tempo."""

import pytest

from srag_agente.guardrails.limites import Limites, chamadas_restantes, motivo_de_parada


def test_padroes_do_spec():
    limites = Limites()
    assert (limites.max_chamadas_ferramenta, limites.max_tokens) == (12, 60_000)
    assert (limites.max_voltas_cobertura, limites.max_tentativas_redacao) == (2, 2)


def test_para_ao_atingir_chamadas_ou_tokens():
    limites = Limites(max_chamadas_ferramenta=3, max_tokens=1000)
    assert motivo_de_parada(limites, 2, 500) is None
    assert motivo_de_parada(limites, 2, 999) is None
    assert "chamadas" in motivo_de_parada(limites, 3, 0)
    assert "chamadas" in motivo_de_parada(limites, 4, 0)  # passou do limite: continua parado
    assert "tokens" in motivo_de_parada(limites, 0, 1000)


def test_para_por_tempo():
    limites = Limites(max_segundos=60)
    assert motivo_de_parada(limites, 0, 0, segundos=59.9) is None
    assert "segundos" in motivo_de_parada(limites, 0, 0, segundos=60)


def test_chamadas_restantes_corta_o_lote_paralelo():
    # o orquestrador pode pedir 5 ferramentas de uma vez quando só cabe 1
    limites = Limites(max_chamadas_ferramenta=12)
    assert chamadas_restantes(limites, 11) == 1
    assert chamadas_restantes(limites, 12) == 0
    assert chamadas_restantes(limites, 15) == 0


@pytest.mark.parametrize("chamadas,tokens,segundos", [(-1, 0, 0), (0, -1, 0), (0, 0, -1)])
def test_contagem_negativa_e_erro_de_programacao(chamadas, tokens, segundos):
    with pytest.raises(ValueError):
        motivo_de_parada(Limites(), chamadas, tokens, segundos=segundos)


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("max_chamadas_ferramenta", 0),
        ("max_tokens", 0),
        ("max_segundos", 0),
        ("max_voltas_cobertura", -1),
        ("max_tentativas_redacao", 0),
        ("max_chamadas_ferramenta", True),
        ("max_segundos", float("nan")),  # nan < 0 e nan == 0 são falsos: passava
        ("max_segundos", float("inf")),
        ("max_tokens", float("nan")),
        ("max_segundos", -1.5),
    ],
)
def test_limite_impossivel_e_recusado(campo, valor):
    with pytest.raises(ValueError):
        Limites(**{campo: valor})
