from srag_agente.metricas.supressao import precisa_suprimir, suprimir


def test_contagem_pequena_vira_menor_que_cinco():
    assert suprimir(3) == "<5"
    assert suprimir(1) == "<5"


def test_zero_e_contagens_grandes_passam():
    assert suprimir(0) == 0
    assert suprimir(5) == 5


def test_precisa_suprimir_se_qualquer_contagem_for_pequena():
    assert precisa_suprimir(100, 4)
    assert not precisa_suprimir(100, 5, 0)
