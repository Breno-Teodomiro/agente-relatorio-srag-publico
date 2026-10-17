from pathlib import Path

import pytest

from srag_agente.rag.indexar import extrair_paginas
from srag_agente.rag.trechos import dividir_em_trechos

CAMPOS = {"EVOLUCAO", "UTI"}
PDF = Path(__file__).parents[2] / "dados/referencia/Dicionario_de_Dados_SRAG_Hospitalizado.pdf"
CAMPOS_DAS_METRICAS = {"EVOLUCAO", "UTI", "VACINA_COV", "CLASSI_FIN"}

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
"""


def por_campo(trechos):
    return {t.campo: t for t in trechos}


def test_cada_campo_vira_um_trecho_com_a_pagina():
    trechos = dividir_em_trechos([PAGINA_1], CAMPOS, minimo_caracteres=10)
    assert [t.campo for t in trechos] == ["EVOLUCAO", "UTI"]
    assert trechos[0].pagina == 1
    assert "Óbito por outras causas" in trechos[0].texto
    assert trechos[0].texto.startswith("Campo EVOLUCAO:")
    assert "Evolução do caso" in trechos[0].texto
    assert "Internado em UTI?" not in trechos[0].texto


def test_palavra_maiuscula_no_meio_da_descricao_nao_corta_o_trecho():
    pagina = (
        "Internado em UTI?\nCaso de\nSRAG\nconfirmado por critério clínico e laboratorial.\nUTI\n"
    )
    trechos = dividir_em_trechos([pagina], CAMPOS, minimo_caracteres=10)
    assert [t.campo for t in trechos] == ["UTI"]
    assert "SRAG" in trechos[0].texto
    assert "confirmado por critério" in trechos[0].texto


def test_a_pagina_do_trecho_e_a_pagina_onde_o_campo_fecha():
    paginas = [
        "Evolução do caso\n1-Cura 2-Óbito 9-Ignorado\nEVOLUCAO\n",
        PAGINA_1.split("EVOLUCAO")[1],
    ]
    trechos = dividir_em_trechos(paginas, CAMPOS, minimo_caracteres=10)
    assert [(t.campo, t.pagina) for t in trechos] == [("EVOLUCAO", 1), ("UTI", 2)]


def test_sobra_final_vira_trecho_sem_campo_na_ultima_pagina():
    paginas = [PAGINA_1, "Texto final explicativo do documento\n"]
    trechos = dividir_em_trechos(paginas, CAMPOS, 10)
    assert trechos[-1].campo is None
    assert trechos[-1].pagina == 2


def test_nome_do_campo_citado_antes_do_minimo_nao_fecha_o_trecho():
    pagina = "Ver UTI\nUTI\nDescrição longa o bastante para passar do mínimo de caracteres.\nUTI\n"
    trechos = dividir_em_trechos([pagina], CAMPOS, minimo_caracteres=40)
    assert len(trechos) == 1
    assert "Ver UTI" in trechos[0].texto


def test_maximo_mantem_o_inicio_do_texto_e_nao_o_fim():
    pagina = "INICIO " + "x" * 100 + " FIM\nUTI\n"
    (trecho,) = dividir_em_trechos([pagina], CAMPOS, minimo_caracteres=10, maximo_caracteres=20)
    assert trecho.texto == "Campo UTI: INICIO xxxxxxxxxxxxx"


def test_cabecalho_de_pagina_nao_entra_no_trecho():
    cab = "SIVEP Gripe- Sistema de Informação da Vigilância Epidemiológica da Gripe.     Página 2 "
    pagina = f"Evolução do caso\n{cab}\n1-Cura 2-Óbito 9-Ignorado\nEVOLUCAO\n"
    (trecho,) = dividir_em_trechos([pagina], CAMPOS, minimo_caracteres=10)
    assert "SIVEP" not in trecho.texto
    assert "1-Cura" in trecho.texto


def test_linha_composta_fecha_o_trecho_e_o_segundo_nome_herda_o_texto():
    pagina = (
        "52-Unidade de Saúde de internação\nID_UN_INTE OU\nCO_UN_INTE\n"
        "53-Internado em UTI?\n1-Sim 2-Não 9-Ignorado\nUTI\n"
    )
    trechos = por_campo(dividir_em_trechos([pagina], CAMPOS, minimo_caracteres=10))
    assert list(trechos) == ["ID_UN_INTE", "CO_UN_INTE", "UTI"]
    assert "52-" in trechos["ID_UN_INTE"].texto
    assert trechos["CO_UN_INTE"].texto == trechos["ID_UN_INTE"].texto.replace(
        "ID_UN_INTE", "CO_UN_INTE", 1
    )
    assert "52-" not in trechos["UTI"].texto
    assert "Internado em UTI" in trechos["UTI"].texto


def test_ou_no_meio_de_frase_nao_e_linha_composta():
    pagina = "Data deve ser maior ou igual a 2-Data de 1ºs sintomas\nCor ou raça\nUTI\n"
    trechos = dividir_em_trechos([pagina], CAMPOS, minimo_caracteres=10)
    assert [t.campo for t in trechos] == ["UTI"]


@pytest.fixture(scope="module")
def trechos_do_pdf():
    return por_campo(dividir_em_trechos(extrair_paginas(PDF), CAMPOS_DAS_METRICAS))


def test_pdf_real_os_campos_das_metricas_existem(trechos_do_pdf):
    assert set(trechos_do_pdf) >= CAMPOS_DAS_METRICAS


def test_pdf_real_trecho_da_uti_nao_mistura_o_campo_52(trechos_do_pdf):
    texto = trechos_do_pdf["UTI"].texto
    assert "Internado em UTI" in texto
    assert "52-" not in texto


def test_pdf_real_nenhum_trecho_leva_o_cabecalho_de_pagina(trechos_do_pdf):
    assert not [
        c for c, t in trechos_do_pdf.items() if "Sistema de Informação da Vigilância" in t.texto
    ]
