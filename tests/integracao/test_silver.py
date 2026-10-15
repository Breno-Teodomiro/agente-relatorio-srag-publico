import threading
from datetime import date

import psycopg
import pytest
from psycopg import sql

from srag_agente.banco import conectar
from srag_agente.dados.carga_raw import carregar_raw
from srag_agente.dados.qualidade import gerar_relatorio_qualidade
from srag_agente.dados.transformar import transformar
from tests.dados import oraculo
from tests.dados.amostra import COLUNAS, escrever_csv

pytestmark = pytest.mark.integracao

SILVER = ("01_silver.sql", "02_qualidade.sql")
SENSIVEIS = {"nu_notific", "dt_nasc", "id_municip", "cs_raca", "cs_sexo"}
COLUNAS_PERMITIDAS = {
    "id_caso", "uf", "faixa_etaria", "dt_notificacao", "dt_sintomas", "dt_internacao",
    "dt_entrada_uti", "dt_evolucao", "hospitalizado", "uti", "suporte_ventilatorio", "evolucao",
    "classificacao", "vacina_gripe", "vacina_covid",
}  # fmt: skip


def _colunas(conn, tabela: str) -> set[str]:
    linhas = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'silver' AND table_name = %s",
        (tabela,),
    ).fetchall()
    return {nome for (nome,) in linhas}


def test_silver_nao_tem_colunas_sensiveis(conn_etl):
    assert _colunas(conn_etl, "casos") & SENSIVEIS == set()


def test_silver_tem_so_as_colunas_permitidas(conn_etl):
    # Lista permitida, não proibida: coluna nova na raw não vaza para a silver por descuido.
    assert _colunas(conn_etl, "casos") == COLUNAS_PERMITIDAS


def test_silver_preserva_todas_as_linhas(conn_etl, banco_carregado):
    total = conn_etl.execute("SELECT count(*) FROM silver.casos").fetchone()[0]
    assert total == len(banco_carregado)


@pytest.mark.parametrize("campo", ["evolucao", "uti", "vacina_covid", "vacina_gripe",
                                   "classificacao", "faixa_etaria"])  # fmt: skip
def test_categorias_batem_com_o_oraculo(conn_etl, banco_carregado, campo):
    esperado = oraculo.contar(oraculo.limpar(banco_carregado), campo)
    obtido = dict(conn_etl.execute(
        f"SELECT {campo}, count(*) FROM silver.casos GROUP BY 1"  # campo vem da lista fixa acima
    ).fetchall())  # fmt: skip
    assert obtido == dict(esperado)


@pytest.mark.parametrize("campo", ["dt_sintomas", "dt_internacao"])
def test_datas_batem_com_o_oraculo(conn_etl, banco_carregado, campo):
    esperado = oraculo.contar(oraculo.limpar(banco_carregado), campo)
    obtido = dict(conn_etl.execute(
        f"SELECT {campo}, count(*) FROM silver.casos GROUP BY 1"  # campo vem da lista fixa acima
    ).fetchall())  # fmt: skip
    assert obtido == dict(esperado)


def test_data_absurda_vira_nula_e_data_com_hora_e_lida(conn_etl):
    qualidade = dict(conn_etl.execute(
        "SELECT coluna, linhas_afetadas FROM silver.qualidade "
        "WHERE regra = 'data_invalida_ou_fora_do_periodo'"
    ).fetchall())  # fmt: skip
    assert qualidade["dt_interna"] == 1  # só o ano 1695; a data com nanossegundos é aproveitada


def test_uf_invalida_vira_nula(conn_etl):
    assert conn_etl.execute("SELECT count(*) FROM silver.casos WHERE uf IS NULL").fetchone()[0] == 1


def test_qualidade_registra_cada_regra(conn_etl):
    regras = dict(conn_etl.execute(
        "SELECT coluna, linhas_afetadas FROM silver.qualidade WHERE regra <> "
        "'data_invalida_ou_fora_do_periodo'"
    ).fetchall())  # fmt: skip
    assert regras["nu_idade_n"] == 1
    assert regras["sg_uf_not"] == 1
    assert regras["evolucao"] == 1


def test_limites_vem_da_notificacao(conn_etl, banco_carregado):
    minimo, maximo = oraculo.limites(banco_carregado)
    assert conn_etl.execute("SELECT minimo, maximo FROM silver.limites").fetchone() == (
        minimo,
        maximo,
    )


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("2025-05-14", date(2025, 5, 14)),
        ("2025-05-14 00:00:00.000000000", date(2025, 5, 14)),
        ("1695-01-17 02:32:37.742690304", None),  # fora do período
        ("2202-06-07", None),  # fora do período
        ("2025-02-30", None),  # dia que não existe: não pode derrubar a carga
        ("14/05/2025", None),  # formato brasileiro não é aceito
        ("2025-W20-3", None),  # semana ISO: o Python aceitaria, a regra não
        ("", None),
        (None, None),
    ],
)
def test_para_data_casos_de_borda(conn_etl, valor, esperado):
    obtido = conn_etl.execute(
        "SELECT silver.para_data(%s, '2024-01-01', '2025-12-31')", (valor,)
    ).fetchone()[0]
    assert obtido == esperado
    assert oraculo.para_data(valor, date(2024, 1, 1), date(2025, 12, 31)) == esperado


def test_para_data_sem_limites_falha_fechado(conn_etl):
    # Raw sem nenhuma DT_NOTIFIC válida → limites nulos → nenhuma data passa sem conferência.
    assert conn_etl.execute("SELECT silver.para_data('2025-05-14', NULL, NULL)").fetchone() == (
        None,
    )


@pytest.mark.parametrize(
    ("n", "tp", "esperado"),
    [
        ("0", "3", "<1"),
        ("11", "2", "<1"),
        ("13", "2", "1-4"),  # há meses ≥ 12 na base real: viram anos, não "<1"
        ("63", "2", "5-11"),
        ("400", "1", "1-4"),  # 400 dias = 1 ano
        ("4", "3", "1-4"),
        ("12", "3", "12-17"),
        ("40", "3", "40-59"),
        ("120", "3", "60+"),
        ("150", "3", "ignorado"),  # acima de 120 anos
        ("-9", "2", "ignorado"),  # há na base real
        ("40", "", "ignorado"),
        ("", "3", "ignorado"),
        (None, None, "ignorado"),
    ],
)
def test_faixa_etaria_casos_de_borda(conn_etl, n, tp, esperado):
    obtido = conn_etl.execute("SELECT silver.faixa_etaria(%s, %s)", (n, tp)).fetchone()[0]
    assert obtido == esperado
    assert oraculo.faixa(n, tp) == esperado


def _foto(conn) -> tuple:
    casos = conn.execute(
        "SELECT count(*), count(dt_internacao), count(uf), "
        "string_agg(DISTINCT faixa_etaria, ',' ORDER BY faixa_etaria) FROM silver.casos"
    ).fetchone()
    qualidade = conn.execute(
        "SELECT regra, coluna, linhas_afetadas, total_linhas FROM silver.qualidade ORDER BY 1, 2"
    ).fetchall()
    return casos, qualidade


def test_transformar_e_idempotente(cfg_teste, banco_carregado):
    with conectar(cfg_teste, "etl", autocommit=True) as conn:
        antes = _foto(conn)
        transformar(conn, SILVER)
        transformar(conn, SILVER)
        assert _foto(conn) == antes


def test_transformar_que_falha_no_meio_nao_muda_nada(cfg_teste, banco_carregado):
    # Autocommit é o caso difícil: sem bloco de transação, o 01 ficaria gravado sozinho.
    with conectar(cfg_teste, "etl", autocommit=True) as conn:
        oid_antes = conn.execute("SELECT 'silver.casos'::regclass::oid").fetchone()[0]
        with pytest.raises(FileNotFoundError):
            transformar(conn, ("01_silver.sql", "nao_existe.sql"))
        assert conn.execute("SELECT 'silver.casos'::regclass::oid").fetchone()[0] == oid_antes


def test_dois_transformar_ao_mesmo_tempo_nao_quebram(cfg_teste, banco_carregado):
    erros: list[BaseException] = []
    largada = threading.Barrier(2)

    def rodar() -> None:
        try:
            with conectar(cfg_teste, "etl", autocommit=True) as conn:
                largada.wait()
                transformar(conn, SILVER)
        except BaseException as erro:  # o teste quer ver qualquer falha das duas threads
            erros.append(erro)

    threads = [threading.Thread(target=rodar) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "transformar travou (deadlock?)"
    assert erros == []
    with conectar(cfg_teste, "etl") as conn:
        total = conn.execute("SELECT count(*) FROM silver.casos").fetchone()[0]
    assert total == len(banco_carregado)


def test_recarga_da_raw_seguida_de_transformar_reconstroi(cfg_teste, banco_carregado, tmp_path):
    # A recarga da raw faz DROP ... CASCADE. A silver é tabela (não depende da raw), sobrevive
    # à recarga e o transformar a reconstrói por inteiro a partir da raw nova.
    csv = escrever_csv(tmp_path / "recarga.csv", banco_carregado)
    with conectar(cfg_teste, "etl", autocommit=True) as conn:
        antes = _foto(conn)
        carregar_raw(conn, csv)
        transformar(conn, SILVER)
        assert _foto(conn) == antes


def test_relatorio_de_qualidade_sai_do_banco(conn_etl, banco_carregado, tmp_path):
    destino = gerar_relatorio_qualidade(conn_etl, tmp_path / "docs" / "QUALIDADE_DADOS.md")
    texto = destino.read_text(encoding="utf-8")
    assert texto.startswith("# Qualidade dos dados")
    assert f"Linhas na raw: **{len(banco_carregado):,}**".replace(",", ".") in texto
    assert "| data_invalida_ou_fora_do_periodo | `dt_interna` | 1 |" in texto
    assert "Não editar à mão" in texto


def test_agente_nao_le_a_silver(cfg_teste, banco_carregado):
    with (
        conectar(cfg_teste, "agente", autocommit=True) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute("SELECT count(*) FROM silver.casos")


# --- Janela de datas robusta a notificação absurda ------------------------------------------
# Valores escritos à mão a partir da amostra: notificações de 2025-01-02 a 2025-06-26,
# mediana 2025-04-16, último sintoma em 2025-06-25. 2025-01-02 - 365 dias = 2024-01-03.
JANELA_DA_AMOSTRA = (date(2024, 1, 3), date(2025, 6, 26))
HOJE_DA_AMOSTRA = date(2025, 6, 25)
ABSURDAS = {
    "1695": "1695-01-17 02:32:37.742690304",
    "2202": "2202-06-07",
}


def _linha_absurda(numero: str, valor: str) -> dict[str, str]:
    linha = dict.fromkeys(COLUNAS, "")
    linha.update({
        "NU_NOTIFIC": numero, "DT_NOTIFIC": valor, "DT_SIN_PRI": valor, "DT_INTERNA": valor,
        "SG_UF_NOT": "SP", "NU_IDADE_N": "40", "TP_IDADE": "3",
    })  # fmt: skip
    return linha


def _inserir_na_raw(conn, linhas: list[dict[str, str]]) -> None:
    colunas = sql.SQL(", ").join(sql.Identifier(c.lower()) for c in COLUNAS)
    marcadores = sql.SQL(", ").join(sql.Placeholder() * len(COLUNAS))
    comando = sql.SQL("INSERT INTO raw.srag ({}) VALUES ({})").format(colunas, marcadores)
    for linha in linhas:
        conn.execute(comando, [linha[c] for c in COLUNAS])


@pytest.mark.parametrize("anos", [["1695"], ["2202"], ["1695", "2202"]])
def test_notificacao_absurda_nao_move_a_janela_nem_o_hoje(cfg_teste, banco_carregado, anos):
    extras = [_linha_absurda(f"8{i}", ABSURDAS[ano]) for i, ano in enumerate(anos)]
    assert oraculo.limites(banco_carregado + extras) == JANELA_DA_AMOSTRA

    # Tudo numa transação desfeita no fim: a amostra da sessão não muda.
    with conectar(cfg_teste, "etl") as conn:
        try:
            _inserir_na_raw(conn, extras)
            transformar(conn, SILVER)
            assert conn.execute("SELECT minimo, maximo FROM silver.limites").fetchone() == (
                JANELA_DA_AMOSTRA
            )
            assert conn.execute("SELECT max(dt_sintomas) FROM silver.casos").fetchone()[0] == (
                HOJE_DA_AMOSTRA
            )
            nulas = conn.execute(
                "SELECT count(*) FILTER (WHERE dt_notificacao IS NULL), "
                "count(*) FILTER (WHERE dt_sintomas IS NULL), "
                "count(*) FILTER (WHERE dt_internacao IS NULL AND hospitalizado = 'ignorado') "
                "FROM silver.casos"
            ).fetchone()
            assert nulas == (len(anos), len(anos), len(anos))
            descartadas = conn.execute(
                "SELECT linhas_afetadas FROM silver.qualidade "
                "WHERE regra = 'notificacao_atipica_fora_do_calculo_da_janela'"
            ).fetchone()[0]
            assert descartadas == len(anos)
        finally:
            conn.rollback()


def test_sem_notificacao_valida_a_janela_falha_fechado(cfg_teste, banco_carregado):
    linha = _linha_absurda("81", "")
    linha["DT_SIN_PRI"] = "2025-05-14"
    assert oraculo.limites([linha]) == (None, None)
    with conectar(cfg_teste, "etl") as conn:
        try:
            conn.execute("DELETE FROM raw.srag")
            _inserir_na_raw(conn, [linha])
            transformar(conn, SILVER)
            assert conn.execute("SELECT minimo, maximo FROM silver.limites").fetchone() == (
                None,
                None,
            )
            assert conn.execute("SELECT count(dt_sintomas) FROM silver.casos").fetchone() == (0,)
        finally:
            conn.rollback()


def test_oraculo_le_notificacao_com_hora_e_usa_a_mediana_inferior():
    # Número par de notificações: a mediana é a menor das duas do meio (como percentile_disc).
    linhas = [
        {"DT_NOTIFIC": "2025-03-01 00:00:00.000000000"},
        {"DT_NOTIFIC": "2025-03-10"},
        {"DT_NOTIFIC": "2025-03-20"},
        {"DT_NOTIFIC": "2026-03-10"},  # a 365 dias da mediana (2025-03-10): ainda plausível
    ]
    assert oraculo.limites(linhas) == (date(2024, 3, 1), date(2026, 3, 10))
    linhas[-1] = {"DT_NOTIFIC": "2026-03-11"}  # a 366 dias: fica fora do cálculo
    assert oraculo.limites(linhas) == (date(2024, 3, 1), date(2025, 3, 20))
