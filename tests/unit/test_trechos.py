from pathlib import Path

import pytest

from srag_agente.rag.indexar import extrair_linhas
from srag_agente.rag.trechos import dividir_em_trechos

CAMPOS = {"EVOLUCAO", "UTI", "CS_SEXO", "ID_MN_RESI", "CO_MUN_RES"}
PDF = Path(__file__).parents[2] / "dados/referencia/Dicionario_de_Dados_SRAG_Hospitalizado.pdf"
CAMPOS_DAS_METRICAS = {"EVOLUCAO", "UTI", "VACINA_COV", "CLASSI_FIN"}

CABECALHO = ["Nome do campo", "Tipo", "Categoria", "Descrição", "Características", "DBF"]
EVOLUCAO = [
    "80-Evolução do caso",
    "Varchar2(1)",
    "1-Cura 2-Óbito 3- Óbito por outras causas 9-Ignorado",
    "Evolução do caso",
    "Campo Essencial",
    "EVOLUCAO",
]
UTI = [
    "53-Internado em UTI?",
    "Varchar2(1)",
    "1-Sim 2-Não 9-Ignorado",
    "O paciente foi internado em UTI?",
    "Campo Essencial",
    "UTI",
]


def por_campo(trechos):
    return {t.campo: t for t in trechos}


def test_cada_linha_vira_um_trecho_com_nome_e_rotulo_no_inicio():
    trechos = dividir_em_trechos([(1, CABECALHO), (1, EVOLUCAO), (2, UTI)], CAMPOS)
    assert [(t.campo, t.pagina) for t in trechos] == [("EVOLUCAO", 1), ("UTI", 2)]
    assert trechos[0].texto.startswith("Campo EVOLUCAO: 80-Evolução do caso.")
    assert "Óbito por outras causas" in trechos[0].texto
    assert "Internado em UTI?" not in trechos[0].texto


def test_linha_cortada_pela_quebra_de_pagina_continua_no_mesmo_campo():
    sexo = [
        "11-Sexo",
        "Varchar2 (1)",
        "1-Masculino",
        "Sexo do paciente.",
        "Campo Obrigatório",
        "CS SEXO _",
    ]
    continuacao = ["", "", "2-Feminino 9-Ignorado", "", "", ""]
    (trecho,) = dividir_em_trechos([(3, sexo), (4, continuacao)], CAMPOS)
    assert trecho.campo == "CS_SEXO"
    assert trecho.pagina == 3
    assert "1-Masculino 2-Feminino 9-Ignorado" in trecho.texto


def test_continuacao_sem_linha_anterior_e_descartada():
    assert dividir_em_trechos([(1, ["", "", "2-Feminino", "", "", ""])], CAMPOS) == []


def test_campo_fora_da_raw_nao_vira_trecho_nem_contamina_o_vizinho():
    cpf = [
        "7-CPF do paciente",
        "Varchar2(15)",
        "Numérico (11 dígitos)",
        "CPF do paciente",
        "",
        "NU CPF _",
    ]
    trechos = dividir_em_trechos([(3, cpf), (3, UTI)], CAMPOS)
    assert [t.campo for t in trechos] == ["UTI"]
    assert "CPF" not in trechos[0].texto


def test_linha_composta_vira_um_trecho_por_nome_com_o_mesmo_texto():
    municipio = [
        "24-Município",
        "Varchar2(6)",
        "",
        "Município de residência do paciente.",
        "",
        "ID MN RESI OU _ _ CO MUN RES _ _",
    ]
    trechos = por_campo(dividir_em_trechos([(6, municipio)], CAMPOS))
    assert list(trechos) == ["ID_MN_RESI", "CO_MUN_RES"]
    assert trechos["CO_MUN_RES"].texto == trechos["ID_MN_RESI"].texto.replace(
        "ID_MN_RESI", "CO_MUN_RES", 1
    )


def test_maximo_mantem_o_inicio_do_texto_e_nao_o_fim():
    longa = [*UTI[:3], "INICIO " + "x" * 300 + " FIM", *UTI[4:]]
    (trecho,) = dividir_em_trechos([(1, longa)], CAMPOS, maximo_caracteres=200)
    assert len(trecho.texto) == 200
    assert trecho.texto.startswith("Campo UTI: 53-Internado em UTI?")
    assert "INICIO" in trecho.texto
    assert "FIM" not in trecho.texto


def test_linha_com_numero_errado_de_colunas_e_ignorada():
    assert dividir_em_trechos([(1, UTI[:5])], CAMPOS) == []


@pytest.fixture(scope="module")
def trechos_do_pdf():
    return por_campo(dividir_em_trechos(extrair_linhas(PDF), CAMPOS_DAS_METRICAS | {"CS_SEXO"}))


def test_pdf_real_os_campos_das_metricas_existem(trechos_do_pdf):
    assert set(trechos_do_pdf) >= CAMPOS_DAS_METRICAS


def test_pdf_real_trecho_da_uti_nao_mistura_o_campo_52(trechos_do_pdf):
    texto = trechos_do_pdf["UTI"].texto
    assert "Internado em UTI" in texto
    assert "52-" not in texto


def test_pdf_real_sexo_traz_as_categorias_da_pagina_seguinte_e_nao_o_cns(trechos_do_pdf):
    texto = trechos_do_pdf["CS_SEXO"].texto
    assert texto.startswith("Campo CS_SEXO: 11-Sexo.")
    assert "2-Feminino" in texto
    assert "Cartão Nacional" not in texto


def test_pdf_real_nenhum_trecho_leva_o_cabecalho_de_pagina(trechos_do_pdf):
    assert not [
        c for c, t in trechos_do_pdf.items() if "Sistema de Informação da Vigilância" in t.texto
    ]
