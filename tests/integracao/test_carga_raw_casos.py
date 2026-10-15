"""Casos que quebram a carga, num banco à parte para não mexer na amostra da sessão."""

import zipfile

import pytest
from psycopg import errors, sql

from srag_agente.banco import conectar
from srag_agente.dados.bootstrap import preparar_banco
from srag_agente.dados.carga_raw import carregar_raw

pytestmark = pytest.mark.integracao

BANCO_CARGA = "srag_teste_carga"
CABECALHO = '"SG_UF_NOT";"ID_MUNICIP"\n'


@pytest.fixture(scope="module")
def cfg_carga(cfg_teste, banco_carregado):
    preparar_banco(cfg_teste, BANCO_CARGA)
    cfg = cfg_teste.model_copy(update={"pg_banco": BANCO_CARGA})
    yield cfg
    with conectar(cfg_teste, "admin", banco="postgres", autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(BANCO_CARGA))
        )


def _csv(tmp_path, texto: str, codificacao: str):
    caminho = tmp_path / f"dados_{codificacao}.csv"
    caminho.write_bytes(texto.encode(codificacao))
    return caminho


def _municipios(cfg) -> list[str]:
    with conectar(cfg, "etl") as conn:
        return [m for (m,) in conn.execute("SELECT id_municip FROM raw.srag ORDER BY 1")]


@pytest.mark.parametrize("codificacao", ["utf-8", "latin-1"])
def test_acentos_chegam_intactos_em_qualquer_codificacao(cfg_carga, tmp_path, codificacao):
    origem = _csv(tmp_path, CABECALHO + '"SP";"SÃO PAULO"\n"PI";"PIAUÍ"\n', codificacao)
    with conectar(cfg_carga, "etl") as conn:
        assert carregar_raw(conn, origem) == 2
    assert _municipios(cfg_carga) == ["PIAUÍ", "SÃO PAULO"]


def test_carga_direto_do_zip(cfg_carga, tmp_path):
    origem = tmp_path / "dados.zip"
    with zipfile.ZipFile(origem, "w", compression=zipfile.ZIP_DEFLATED) as arquivo_zip:
        arquivo_zip.writestr("INFLUD.csv", (CABECALHO + '"RJ";"NITERÓI"\n').encode())
    with conectar(cfg_carga, "etl") as conn:
        assert carregar_raw(conn, origem) == 1
    assert _municipios(cfg_carga) == ["NITERÓI"]


def test_recarregar_substitui_em_vez_de_duplicar(cfg_carga, tmp_path):
    origem = _csv(tmp_path, CABECALHO + '"SP";"A"\n"SP";"B"\n"SP";"C"\n', "utf-8")
    for _ in range(2):
        with conectar(cfg_carga, "etl") as conn:
            assert carregar_raw(conn, origem) == 3
    assert _municipios(cfg_carga) == ["A", "B", "C"]


def test_carga_que_falha_no_meio_mantem_a_raw_anterior(cfg_carga, tmp_path):
    boa = _csv(tmp_path, CABECALHO + '"SP";"ANTES"\n', "utf-8")
    with conectar(cfg_carga, "etl") as conn:
        carregar_raw(conn, boa)

    quebrada = tmp_path / "quebrada.csv"
    quebrada.write_bytes((CABECALHO + '"SP";"NOVA"\n"SP";"X";"COLUNA A MAIS"\n').encode())
    with conectar(cfg_carga, "etl") as conn:
        with pytest.raises(errors.BadCopyFileFormat):
            carregar_raw(conn, quebrada)
        # A conexão continua utilizável depois da falha.
        assert conn.execute("SELECT 1").fetchone() == (1,)
    assert _municipios(cfg_carga) == ["ANTES"]


def test_vazios_entre_aspas_ficam_como_texto_vazio(cfg_carga, tmp_path):
    origem = _csv(tmp_path, CABECALHO + '"SP";""\n', "utf-8")
    with conectar(cfg_carga, "etl") as conn:
        carregar_raw(conn, origem)
    assert _municipios(cfg_carga) == [""]


def test_falha_no_meio_em_autocommit_tambem_mantem_a_raw_anterior(cfg_carga, tmp_path):
    boa = _csv(tmp_path, CABECALHO + '"SP";"ANTES"\n', "utf-8")
    with conectar(cfg_carga, "etl", autocommit=True) as conn:
        carregar_raw(conn, boa)

    quebrada = tmp_path / "quebrada.csv"
    quebrada.write_bytes((CABECALHO + '"SP";"NOVA"\n"SP";"X";"COLUNA A MAIS"\n').encode())
    with conectar(cfg_carga, "etl", autocommit=True) as conn:
        with pytest.raises(errors.BadCopyFileFormat):
            carregar_raw(conn, quebrada)
        assert conn.execute("SELECT 1").fetchone() == (1,)
    assert _municipios(cfg_carga) == ["ANTES"]


def test_carga_em_autocommit_fica_gravada(cfg_carga, tmp_path):
    origem = _csv(tmp_path, CABECALHO + '"SP";"GRAVADA"\n', "utf-8")
    with conectar(cfg_carga, "etl", autocommit=True) as conn:
        assert carregar_raw(conn, origem) == 1
    assert _municipios(cfg_carga) == ["GRAVADA"]
