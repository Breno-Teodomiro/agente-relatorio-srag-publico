"""E2E 6 — supressão de células pequenas (LGPD): contagem de 1 a 4 nunca aparece exata.

Piauí, na amostra, tem poucos casos por dia e por faixa etária. O que o SQL da gold entrega com
1 a 4 casos tem de sair como `<5` (e a menor célula visível junto, como `oculto`) no HTML, no
Markdown e na trilha de auditoria; só o total, que é exato, aparece.
"""

import json
import re
from datetime import date
from html import unescape

import pytest

from tests.e2e.apoio import CHAMADAS_PI, Simulacao, eventos_no_banco, jsonl

pytestmark = pytest.mark.integracao


@pytest.fixture(scope="module")
def piaui(cfg_teste, banco_carregado, tmp_path_factory):
    simulacao = Simulacao(uf="PI", chamadas=list(CHAMADAS_PI))
    simulacao.rodar(cfg_teste, tmp_path_factory.mktemp("piaui"))
    return simulacao


@pytest.fixture
def faixas_do_sql(conn_etl):
    """Casos por faixa etária no PI, direto da gold (nomes como a tabela do relatório os exibe)."""
    conn_etl.commit()
    return dict(conn_etl.execute(
        "SELECT categoria, casos FROM gold.distribuicao "
        "WHERE dimensao = 'faixa_etaria' AND uf = 'PI'").fetchall())  # fmt: skip


def _celulas_md(markdown: str) -> dict[str, str]:
    tabela = markdown.split("por faixa etária")[1].split("*Total")[0]
    linhas = re.findall(r"^\| ([^|]+) \| ([^|]+) \| [^|]+ \|$", tabela, re.MULTILINE)
    return {categoria.strip(): casos.strip() for categoria, casos in linhas
            if categoria.strip() not in ("Categoria", "---")}  # fmt: skip


def _celulas_html(html: str) -> dict[str, str]:
    tabela = html.split("Por faixa etária")[1].split("</table>")[0]
    linhas = re.findall(r'<tr><td>([^<]+)</td><td class="num">([^<]+)</td>', tabela)
    return {unescape(categoria): unescape(casos) for categoria, casos in linhas}


def test_a_amostra_tem_faixa_pequena_no_piaui(faixas_do_sql):
    """Pré-condição do cenário: sem ela, os testes abaixo não provariam nada."""
    assert any(0 < casos < 5 for casos in faixas_do_sql.values())


def test_faixa_com_poucos_casos_sai_como_menor_que_5_no_html_e_no_markdown(piaui, faixas_do_sql):
    for celulas in (_celulas_md(piaui.markdown), _celulas_html(piaui.html)):
        assert set(celulas) == set(faixas_do_sql)
        for faixa, casos in faixas_do_sql.items():
            if 0 < casos < 5:
                assert celulas[faixa] == "<5", f"{faixa}: {celulas[faixa]!r}"
            # nenhuma célula exibe uma contagem pequena exata
            assert celulas[faixa] not in {"1", "2", "3", "4"}
        # supressão complementar: o total é exato e há uma célula "oculto" para fechar a conta
        assert "oculto" in celulas.values()
    assert f"Total: {sum(faixas_do_sql.values())}" in piaui.markdown


def test_serie_diaria_do_piaui_nao_exibe_contagem_pequena_exata(piaui, conn_etl):
    diaria = next(s for s in piaui.contextos[0].series if s["tipo"] == "diaria_30d")
    referencia = date.fromisoformat(diaria["data_referencia"])
    sql = dict(conn_etl.execute(
        "SELECT dia, casos FROM gold.casos_diarios WHERE uf = 'PI' AND dia BETWEEN %s - 29 AND %s",
        (referencia, referencia)).fetchall())  # fmt: skip
    pequenos = {dia for dia, casos in sql.items() if 0 < casos < 5}
    assert pequenos, "pré-condição: o PI tem dias com 1 a 4 casos"
    for ponto in diaria["pontos"]:
        if date.fromisoformat(ponto["rotulo"]) in pequenos:
            assert ponto["casos"] == "<5"
    # no gráfico (SVG do HTML) a legenda explica o rótulo e nenhuma barra traz o número pequeno
    assert "Menos de 5 casos (&lt;5)" in piaui.html
    textos_svg = re.findall(r"<text[^>]*>([^<]*)</text>", piaui.html.split("Casos mensais")[0])
    assert not {t for t in textos_svg if t in {"1", "2", "3", "4"}}


def test_trilha_do_banco_e_do_jsonl_guardam_a_contagem_ja_suprimida(piaui, conn_etl,
                                                                    faixas_do_sql):  # fmt: skip
    pequena = next(f for f, casos in faixas_do_sql.items() if 0 < casos < 5)
    no_banco = [e for e in eventos_no_banco(conn_etl, piaui.resultado.id_execucao)
                if e["ferramenta"] == "consultar_distribuicao"
                and e["resultado"]["dimensao"] == "faixa_etaria"]  # fmt: skip
    no_jsonl = [r for r in jsonl(piaui.resultado.arquivos.auditoria)
                if r.get("ferramenta") == "consultar_distribuicao"
                and r["resultado"]["dimensao"] == "faixa_etaria"]  # fmt: skip
    for evento in (*no_banco, *no_jsonl):
        categorias = {c["categoria"]: c["casos"] for c in evento["resultado"]["categorias"]}
        assert categorias[pequena] == "<5", json.dumps(evento["resultado"])[:300]
    assert no_banco and no_jsonl
