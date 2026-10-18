"""E2E 1 — caminho feliz: núcleo real contra o `srag_teste`, LLM e notícias falsos."""

import re
from datetime import date

import pytest

from srag_agente.relatorio.montar import AVISO_FIXO
from tests.e2e.apoio import (
    Simulacao,
    arquivos_gerados,
    eventos_no_banco,
    jsonl,
)
from tests.fakes import CHAMADAS_COMPLETAS, NOTICIAS

pytestmark = pytest.mark.integracao


@pytest.fixture(scope="module")
def feliz(cfg_teste, banco_carregado, tmp_path_factory):
    simulacao = Simulacao()
    simulacao.rodar(cfg_teste, tmp_path_factory.mktemp("feliz"))
    return simulacao


@pytest.fixture
def gold(conn_etl):
    """Os números como o SQL da gold os entrega, lidos direto das views (sem passar pelo agente)."""
    indicadores = dict(zip(
        ("obitos", "com_desfecho", "uti_sim", "uti_informada", "covid_sim", "covid_informada",
         "gripe_sim", "gripe_informada"),
        conn_etl.execute(
            "SELECT obitos, com_desfecho, uti_sim, uti_informada, vacina_covid_sim, "
            "vacina_covid_informada, vacina_gripe_sim, vacina_gripe_informada "
            "FROM gold.indicadores WHERE uf = 'BR'").fetchone(),
        strict=True))  # fmt: skip
    atual, anterior = conn_etl.execute(
        "SELECT casos_7d_atual, casos_7d_anterior FROM gold.janelas_aumento WHERE uf = 'BR'"
    ).fetchone()
    referencia = conn_etl.execute("SELECT data_referencia, total_casos FROM gold.referencia")
    return {**indicadores, "atual": atual, "anterior": anterior,
            "referencia": referencia.fetchone()}  # fmt: skip


def _pct(numerador: int, denominador: int) -> str:
    return f"{100 * numerador / denominador:.1f}".replace(".", ",") + "%"


def _sem_espaco_duplo(texto: str) -> str:
    return re.sub(r"\s+", " ", texto)


def test_gera_relatorio_graficos_e_trilha(feliz):
    assert feliz.resultado.status == "ok"
    arquivos = arquivos_gerados(feliz.resultado.arquivos.pasta)
    assert set(arquivos) == {"relatorio.html", "relatorio.md", "auditoria.jsonl",
                             "graficos/diaria_30d.png", "graficos/mensal_12m.png"}  # fmt: skip
    for grafico in ("graficos/diaria_30d.png", "graficos/mensal_12m.png"):
        assert arquivos[grafico].startswith(b"\x89PNG\r\n\x1a\n")
    assert AVISO_FIXO in feliz.html and AVISO_FIXO in feliz.markdown


def test_numeros_do_relatorio_batem_com_o_sql_da_gold(feliz, gold):
    esperado = [
        f"{_pct(gold['obitos'], gold['com_desfecho'])}",
        f"{gold['obitos']} de {gold['com_desfecho']}",
        f"{_pct(gold['uti_sim'], gold['uti_informada'])}",
        f"{gold['uti_sim']} de {gold['uti_informada']}",
        f"{_pct(gold['covid_sim'], gold['covid_informada'])}",
        f"{gold['covid_sim']} de {gold['covid_informada']}",
        f"{gold['atual']} casos contra {gold['anterior']} na semana anterior",
        "+" + _pct(gold["atual"] - gold["anterior"], gold["anterior"]),
    ]
    html = _sem_espaco_duplo(feliz.html)
    for trecho in esperado:
        assert trecho in feliz.markdown, f"{trecho!r} fora do Markdown"
        assert trecho in html, f"{trecho!r} fora do HTML"
    assert f"{gold['referencia'][0]:%d/%m/%Y}" in feliz.markdown


def test_quatro_metricas_e_duas_series_aparecem(feliz):
    nomes = ("aumento de casos", "mortalidade", "internação em UTI", "vacinação contra COVID-19")
    for nome in nomes:
        assert nome in feliz.markdown and nome in feliz.html
    assert "graficos/diaria_30d.png" in feliz.markdown
    assert "graficos/mensal_12m.png" in feliz.markdown
    assert feliz.html.count("<svg") == 4  # 2 gráficos x (tema escuro + tema claro)
    assert "Casos diários de SRAG — 30 dias" in feliz.html
    assert "Casos mensais de SRAG — últimos 12 meses" in feliz.html


def test_series_batem_com_o_sql_da_gold(feliz, conn_etl, gold):
    diaria, mensal = (next(s for s in feliz.contextos[0].series if s["tipo"] == t)
                      for t in ("diaria_30d", "mensal_12m"))  # fmt: skip
    referencia = gold["referencia"][0]
    assert len(diaria["pontos"]) == 30 and len(mensal["pontos"]) == 12
    sql_diario = dict(conn_etl.execute(
        "SELECT dia, casos FROM gold.casos_diarios WHERE uf = 'BR' AND dia BETWEEN %s - 29 AND %s",
        (referencia, referencia)).fetchall())  # fmt: skip
    for ponto in diaria["pontos"]:
        casos = ponto["casos"]
        real = sql_diario.get(date.fromisoformat(ponto["rotulo"]), 0)
        if isinstance(casos, int):
            assert casos == real
        else:  # supressão: "<5" esconde 1 a 4; "oculto" esconde um valor de 5 ou mais
            assert (0 < real < 5) if casos == "<5" else real >= 5
    sql_mensal = dict(conn_etl.execute(
        "SELECT to_char(dia, 'YYYY-MM'), sum(casos)::int FROM gold.casos_diarios "
        "WHERE uf = 'BR' GROUP BY 1").fetchall())  # fmt: skip
    for ponto in mensal["pontos"]:
        if isinstance(ponto["casos"], int):
            assert ponto["casos"] == sql_mensal[ponto["rotulo"]]
        elif ponto["fora_da_base"]:
            assert ponto["rotulo"] not in sql_mensal


def test_execucao_fica_ok_no_banco_com_fim_casos_e_tokens(feliz, conn_etl, gold):
    conn_etl.commit()
    status, fim, total_casos, entrada, saida, custo, versao, prompts = conn_etl.execute(
        "SELECT status, fim, total_casos, tokens_entrada, tokens_saida, custo_usd, versao_codigo, "
        "hash_prompts FROM auditoria.execucoes WHERE id_execucao = %s",
        (feliz.resultado.id_execucao,)).fetchone()  # fmt: skip
    assert status == "ok" and fim is not None
    assert total_casos == gold["referencia"][1]
    assert (entrada, saida) == (feliz.resultado.tokens_entrada, feliz.resultado.tokens_saida)
    assert entrada > 0 and saida > 0 and float(custo) > 0
    assert versao and prompts


def test_todo_evento_de_ferramenta_tem_motivo_no_banco_e_no_jsonl(feliz, conn_etl):
    no_banco = [e for e in eventos_no_banco(conn_etl, feliz.resultado.id_execucao)
                if e["tipo"] == "ferramenta"]  # fmt: skip
    assert len(no_banco) == len(CHAMADAS_COMPLETAS)
    assert all(e["motivo"] and e["ferramenta"] and not e["erro"] for e in no_banco)
    registros = jsonl(feliz.resultado.arquivos.auditoria)
    assert registros[0]["tipo_registro"] == "execucao" and registros[0]["status"] == "ok"
    eventos = [r for r in registros if r["tipo_registro"] == "evento"]
    assert [e["sequencia"] for e in eventos] == list(range(1, len(eventos) + 1))
    assert len(eventos) == len(eventos_no_banco(conn_etl, feliz.resultado.id_execucao))
    assert all(e["motivo"] for e in eventos if e["tipo"] == "ferramenta")


def test_noticias_citadas_existem_nos_resultados_da_busca(feliz):
    urls_da_busca = {n.url for n in NOTICIAS.values()}
    referencias = set(re.findall(r'id="ref-(\d+)"', feliz.html))
    citadas = set(re.findall(r'href="#ref-(\d+)"', feliz.html))
    assert citadas == referencias == {"1", "2"}  # uma de cada janela, todas citadas
    links = set(re.findall(r'<a href="(https?://[^"]+)"', feliz.html))
    assert links == urls_da_busca
    assert {termo for termo, *_ in feliz.buscador.chamadas} == {"SRAG influenza", "SRAG"}
