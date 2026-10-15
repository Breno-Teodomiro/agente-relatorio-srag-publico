"""Casos de borda da leitura do CSV, sem banco."""

import io
import zipfile

import pytest

from srag_agente.dados.carga_raw import abrir_csv, detectar_codificacao, ler_cabecalho


def test_cabecalho_entre_aspas_vira_minusculo():
    fluxo = io.BytesIO(b'"NU_NOTIFIC";"DT_SIN_PRI";"SG_UF_NOT"\r\n"1";"2025-01-01";"SP"\n')
    assert ler_cabecalho(fluxo) == ["nu_notific", "dt_sin_pri", "sg_uf_not"]
    assert fluxo.readline() == b'"1";"2025-01-01";"SP"\n'


@pytest.mark.parametrize(
    "cabecalho",
    [
        b"",
        b"\n",
        b'\xef\xbb\xbf"NU_NOTIFIC";"DT_SIN_PRI"\n',
        b'"NU_NOTIFIC";"x text); DROP TABLE raw.srag; --"\n',
        b'"NU_NOTIFIC";;"DT_SIN_PRI"\n',
    ],
    ids=["vazio", "linha-em-branco", "bom-utf8", "nome-malicioso", "coluna-sem-nome"],
)
def test_cabecalho_inesperado_e_recusado(cabecalho):
    with pytest.raises(ValueError, match="Cabeçalho"):
        ler_cabecalho(io.BytesIO(cabecalho))


def test_cabecalho_com_coluna_repetida_e_recusado():
    with pytest.raises(ValueError, match="repetid"):
        ler_cabecalho(io.BytesIO(b'"A";"B";"a"\n'))


def test_abre_o_csv_de_dentro_do_zip(tmp_path):
    origem = tmp_path / "dados.zip"
    with zipfile.ZipFile(origem, "w") as arquivo_zip:
        arquivo_zip.writestr("leia-me.txt", "nada")
        arquivo_zip.writestr("INFLUD.CSV", '"A";"B"\n"1";"2"\n')
    with abrir_csv(origem) as fluxo:
        assert fluxo.read() == b'"A";"B"\n"1";"2"\n'


def test_zip_sem_csv_da_erro_claro(tmp_path):
    origem = tmp_path / "dados.zip"
    with zipfile.ZipFile(origem, "w") as arquivo_zip:
        arquivo_zip.writestr("leia-me.txt", "nada")
    with pytest.raises(ValueError, match="nenhum CSV"), abrir_csv(origem):
        pass


def test_zip_com_mais_de_um_csv_e_recusado(tmp_path):
    origem = tmp_path / "dados.zip"
    with zipfile.ZipFile(origem, "w") as arquivo_zip:
        arquivo_zip.writestr("a.csv", "x")
        arquivo_zip.writestr("b.csv", "y")
    with pytest.raises(ValueError, match="mais de um CSV"), abrir_csv(origem):
        pass


@pytest.mark.parametrize(
    ("conteudo", "esperada"),
    [
        ('"UF"\n"SP"\n'.encode("latin-1"), "UTF8"),
        ('"MUNICIPIO"\n"SÃO PAULO"\n'.encode(), "UTF8"),
        ('"MUNICIPIO"\n"SÃO PAULO"\n'.encode("latin-1"), "LATIN1"),
    ],
    ids=["so-ascii", "utf8-com-acento", "latin1-com-acento"],
)
def test_detecta_a_codificacao_do_arquivo(tmp_path, conteudo, esperada):
    origem = tmp_path / "dados.csv"
    origem.write_bytes(conteudo)
    assert detectar_codificacao(origem) == esperada


def test_caractere_utf8_partido_entre_blocos_continua_utf8(tmp_path):
    # "Ã" ocupa 2 bytes; com bloco de 1 byte, ele sempre cai partido entre duas leituras.
    origem = tmp_path / "dados.csv"
    origem.write_bytes('"SÃO"\n'.encode())
    assert detectar_codificacao(origem, tamanho_bloco=1) == "UTF8"
