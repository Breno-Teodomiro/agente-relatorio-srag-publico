from srag_agente.rag.trechos import dividir_em_trechos

CAMPOS = {"EVOLUCAO", "UTI"}

PAGINA_1 = """Evolução do caso
Varchar2(1)
1-Cura
2-Óbito
3- Óbito por outras causas
9-Ignorado
EVOLUCAO
Internado em UTI?
1-Sim 2-Não 9-Ignorado
O paciente foi internado em UTI?
UTI
SRAG
"""


def test_cada_campo_vira_um_trecho_com_a_pagina():
    trechos = dividir_em_trechos([PAGINA_1], CAMPOS, minimo_caracteres=10)
    campos = [t.campo for t in trechos]
    assert campos[:2] == ["EVOLUCAO", "UTI"]
    assert trechos[0].pagina == 1
    assert "Óbito por outras causas" in trechos[0].texto
    assert trechos[0].texto.startswith("Campo EVOLUCAO:")


def test_palavra_maiuscula_que_nao_e_campo_nao_corta_o_trecho():
    trechos = dividir_em_trechos([PAGINA_1], CAMPOS, minimo_caracteres=10)
    assert all(t.campo != "SRAG" for t in trechos)


def test_sobra_final_vira_trecho_sem_campo():
    trechos = dividir_em_trechos([PAGINA_1 + "Texto final explicativo do documento"], CAMPOS, 10)
    assert trechos[-1].campo is None
