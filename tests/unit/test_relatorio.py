import base64
import hashlib
import html as htmllib
import re
from copy import deepcopy
from dataclasses import replace

import pytest

from srag_agente.auditoria.registro import Auditoria
from srag_agente.modelos import NoticiaCitavel
from srag_agente.relatorio.formatacao import data_br, inteiro, mes_br, pct, variacao
from srag_agente.relatorio.graficos import _altura, grafico_diario, grafico_mensal
from srag_agente.relatorio.montar import AVISO_FIXO, montar_dados, montar_relatorio
from tests.fakes import HOJE, evidencias_completas, texto_valido


def test_formatacao_brasileira():
    assert pct(0.0796) == "8,0%" and pct(None) == "—"
    assert variacao(0.2) == "+20,0%" and variacao(-0.125) == "−12,5%"  # noqa: RUF001
    assert variacao(None) == "—"
    assert inteiro(53476) == "53.476" and inteiro("<5") == "<5" and inteiro("oculto") == "oculto"
    assert data_br("2025-06-25") == "25/06/2025" and mes_br("2025-06") == "jun/25"


@pytest.fixture
def evidencias():
    return evidencias_completas()


def _serie(evidencias, tipo):
    return next(e.dados for e in evidencias if e.dados.get("tipo") == tipo)


def test_graficos_saem_em_svg_e_png(evidencias):
    assert b"<svg" in grafico_diario(_serie(evidencias, "diaria_30d"), "escuro")
    assert grafico_mensal(_serie(evidencias, "mensal_12m"), "claro", "png").startswith(b"\x89PNG")


def test_grafico_aguenta_valor_suprimido(evidencias):
    serie = _serie(evidencias, "diaria_30d")
    serie = {**serie, "pontos": [{**p, "casos": "<5"} for p in serie["pontos"]]}
    assert b"<svg" in grafico_diario(serie, "claro")


@pytest.mark.parametrize(
    ("tipo", "desenhar"), [("diaria_30d", grafico_diario), ("mensal_12m", grafico_mensal)]
)
def test_suprimido_tem_marca_e_legenda_proprias(evidencias, tipo, desenhar):
    serie = deepcopy(_serie(evidencias, tipo))
    com_dados = [p for p in serie["pontos"] if not p["fora_da_base"]]
    com_dados[0]["casos"] = "<5"
    com_dados[1]["casos"] = "oculto"
    svg = desenhar(serie, "escuro").decode("utf-8")
    assert "Menos de 5 casos (&lt;5)" in svg and "Oculto (supressão complementar)" in svg
    limpo = desenhar(_serie(evidencias, tipo), "escuro").decode("utf-8")
    assert "Menos de 5 casos" not in limpo and "Oculto (" not in limpo


_PNG_MINIMO = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def graficos_falsos(monkeypatch):
    """Dublê dos gráficos: os testes que só olham texto não pagam a renderização do matplotlib."""

    def desenhar(serie, tema, formato="svg"):
        return b"<svg></svg>" if formato == "svg" else _PNG_MINIMO

    monkeypatch.setattr(
        "srag_agente.relatorio.montar.GRAFICOS",
        (("diaria_30d", desenhar), ("mensal_12m", desenhar)),
    )


def _montar(tmp_path, evidencias, **mudar):
    auditoria = Auditoria()
    auditoria.iniciar_execucao(
        provedor="openrouter",
        modelo="openai/gpt-4.1-mini",
        parametros={},
        versao_codigo="abc",
        hash_prompts="h",
    )
    auditoria.registrar(
        "ferramentas",
        "ferramenta",
        ferramenta="consultar_metricas",
        motivo="preciso da mortalidade",
    )
    campos = dict(
        evidencias=evidencias,
        id_execucao=auditoria.id_execucao,
        status="ok",
        uf="BR",
        data_execucao=HOJE,
        texto=texto_valido(),
        avisos=[],
        violacoes=[],
        auditoria=auditoria,
        provedor="openrouter",
        modelo="openai/gpt-4.1-mini",
        tokens_entrada=1000,
        tokens_saida=200,
        custo_usd=0.00072,
    )
    campos.update(mudar)
    return montar_relatorio(montar_dados(**campos), auditoria, tmp_path)


def test_relatorio_tem_tudo_o_que_o_desafio_pede(tmp_path, evidencias):
    arquivos = _montar(tmp_path, evidencias)
    html = arquivos.html.read_text(encoding="utf-8")
    for esperado in (
        "Taxa de aumento de casos",
        "Taxa de mortalidade",
        "Taxa de internação em UTI",
        "Taxa de vacinação",
        "+20,0%",
        "10,0%",
        "<svg",
        AVISO_FIXO,
        'href="#ref-1"',
        "Como este relatório foi gerado",
        "preciso da mortalidade",
    ):
        assert esperado in html, esperado
    markdown = arquivos.markdown.read_text(encoding="utf-8")
    assert "graficos/diaria_30d.png" in markdown and "graficos/mensal_12m.png" in markdown
    assert (arquivos.pasta / "graficos" / "diaria_30d.png").exists()
    assert len(arquivos.auditoria.read_text().splitlines()) == 2


def test_texto_do_llm_e_escapado_no_html(tmp_path, evidencias, graficos_falsos):
    texto = texto_valido(resumo_executivo="<script>alert(1)</script> Casos estáveis.")
    html = _montar(tmp_path, evidencias, texto=texto).html.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_todo_texto_externo_e_escapado_e_nada_ativo_vem_de_fora(
    tmp_path, evidencias, graficos_falsos
):
    ataque = '<img src=x onerror=alert(1)><script>alert(2)</script>"><svg onload=alert(3)>'
    texto = texto_valido(
        cenario_atual=ataque + " [2]",
        contexto_periodo_dados=ataque + " [1]",
        limitacoes=ataque,
        resposta_pergunta_extra=ataque,
        comentarios_metricas=[
            {"metrica": m, "comentario": ataque}
            for m in ("aumento_casos", "mortalidade", "uti", "vacinacao")
        ],
    )
    for e in evidencias:
        if e.ferramenta == "buscar_noticias":
            e.dados["noticias"][0]["titulo"] = ataque
            e.dados["noticias"][0]["fonte"] = ataque
    html = _montar(
        tmp_path,
        evidencias,
        texto=texto,
        avisos=[ataque],
        violacoes=[ataque],
        status="revisao_humana",
        pergunta_extra=ataque,
    ).html.read_text(encoding="utf-8")
    assert "<img" not in html and "&lt;img src=x" in html
    assert "alert(2)</script>" not in html and "<svg onload" not in html
    assert html.count("<script") == 1  # só o do alternador de tema, que é da casa
    assert "Content-Security-Policy" in html and "default-src 'none'" in htmllib.unescape(html)
    # nada carrega recurso de fora (img/script/link/iframe com src ou href externo de página)
    assert not re.search(r"<(img|script|iframe|link|object|embed)[^>]+(src|href)=", html)
    assert not re.search(r"url\(\s*['\"]?https?:", html)


def _com_noticia(url, titulo="Boletim", fonte="Fiocruz"):
    return NoticiaCitavel(
        id=1,
        titulo=titulo,
        fonte=fonte,
        url=url,
        data=None,
        janela="cenario_atual",
        confiavel=True,
        trecho="trecho",
        inicio_janela=None,
        fim_janela=None,
    )


def _montar_com_dados(tmp_path, evidencias, **mudar):
    """Monta a partir de DadosRelatorio já alterado: contorna o filtro de numerar_noticias."""
    auditoria = Auditoria()
    auditoria.iniciar_execucao(
        provedor="p", modelo="m", parametros={}, versao_codigo="abc", hash_prompts="h"
    )
    dados = montar_dados(
        evidencias=evidencias,
        id_execucao=auditoria.id_execucao,
        status="ok",
        uf="BR",
        data_execucao=HOJE,
        texto=texto_valido(),
        avisos=[],
        violacoes=[],
        auditoria=auditoria,
        provedor="p",
        modelo="m",
        tokens_entrada=1,
        tokens_saida=1,
        custo_usd=0.0,
    )
    return montar_relatorio(replace(dados, **mudar), auditoria, tmp_path)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,<b>x</b>", "vbscript:x"])
def test_link_que_nao_e_http_e_neutralizado_nos_dois_templates(
    tmp_path, evidencias, graficos_falsos, url
):
    """Chega ao template sem passar por numerar_noticias: quem barra é o filtro url_segura."""
    arquivos = _montar_com_dados(tmp_path, evidencias, noticias=[_com_noticia(url)])
    esquema = url.split(":")[0] + ":"
    for saida in (arquivos.html, arquivos.markdown):
        texto = saida.read_text(encoding="utf-8")
        assert esquema not in texto.lower().replace('href="#"', ""), saida.name
    assert 'href="#"' in arquivos.html.read_text(encoding="utf-8")
    assert "(<#>)" in arquivos.markdown.read_text(encoding="utf-8")


def test_url_http_com_parenteses_nao_fecha_o_link_do_markdown(
    tmp_path, evidencias, graficos_falsos
):
    url = "https://exemplo.gov.br/a_(b) c<d>?q=1&r=2"
    markdown = _montar_com_dados(
        tmp_path, evidencias, noticias=[_com_noticia(url)]
    ).markdown.read_text(encoding="utf-8")
    assert "(<https://exemplo.gov.br/a_%28b%29%20c%3Cd%3E?q=1&r=2>)" in markdown


def _linhas_md(arquivos):
    return arquivos.markdown.read_text(encoding="utf-8").splitlines()


def test_markdown_nao_aceita_injecao_em_titulo_fonte_e_url(tmp_path, evidencias, graficos_falsos):
    titulo = (
        "Fiocruz…](https://evil.example/a)![x](https://tracker.example/p.png) "
        "<img src=x onerror=alert(1)>)"
    )
    linhas = _linhas_md(
        _montar_com_dados(
            tmp_path, evidencias, noticias=[_com_noticia("https://ok.gov.br/n", titulo, titulo)]
        )
    )
    ref = next(linha for linha in linhas if linha.startswith("1. ["))
    assert "<img" not in ref and "![" not in ref
    assert len(re.findall(r"(?<!\\)\]\(", ref)) == 1  # só o do link de verdade
    assert ref.endswith("(cenário atual)") and "](<https://ok.gov.br/n>)" in ref


def test_markdown_nao_aceita_injecao_em_texto_livre(tmp_path, evidencias, graficos_falsos):
    ataque = (
        "Casos estáveis [1]. ![p](https://tracker.example/q.png) <script>alert(9)</script> "
        "[c](javascript:alert(1))"
    )
    texto = texto_valido(
        resumo_executivo=ataque,
        contexto_periodo_dados=ataque,
        cenario_atual=ataque,
        limitacoes=ataque,
        resposta_pergunta_extra=ataque,
        comentarios_metricas=[
            {"metrica": m, "comentario": ataque}
            for m in ("aumento_casos", "mortalidade", "uti", "vacinacao")
        ],
    )
    arquivos = _montar(
        tmp_path,
        evidencias,
        texto=texto,
        avisos=[ataque, "linha 1\n# Título injetado\n> citação", "# Título puro"],
        violacoes=[ataque, "marcacao_html: '<script…'"],
        status="revisao_humana",
        pergunta_extra=ataque,
    )
    markdown = arquivos.markdown.read_text(encoding="utf-8")
    assert "<script" not in markdown and "<img" not in markdown
    assert "&lt;script&gt;alert(9)&lt;/script&gt;" in markdown
    assert "![" not in markdown.replace("![Casos", "")  # só as duas imagens dos gráficos
    assert not re.search(r"(?<!\\)\]\(", markdown.replace("](<", "").replace("](graficos/", ""))
    assert not re.search(r"(?<!\\)!\[", markdown.replace("![Casos", ""))
    assert "[1]" in markdown  # a citação continua legível
    # aviso com quebra de linha não vira título nem bloco novo
    assert not any(linha.startswith("#") and "injetado" in linha for linha in markdown.splitlines())
    assert (
        "> linha 1 # Título injetado &gt; citação" in markdown and "> \\# Título puro" in markdown
    )


def test_graficos_do_markdown_continuam_imagens(tmp_path, evidencias, graficos_falsos):
    markdown = _montar(tmp_path, evidencias).markdown.read_text(encoding="utf-8")
    assert "![Casos diários de SRAG nos últimos 30 dias](graficos/diaria_30d.png)" in markdown


def test_altura_de_texto_e_none_e_zero():
    assert _altura("<5") == 0.0 and _altura("oculto") == 0.0 and _altura(None) == 0.0
    assert _altura(7) == 7.0


def test_graficos_ids_e_data_estaveis(evidencias):
    serie = _serie(evidencias, "mensal_12m")
    svg = grafico_mensal(serie, "escuro")
    assert svg == grafico_mensal(serie, "escuro")  # sem data nem sal aleatório
    assert b"<dc:date>" not in svg
    texto = svg.decode("utf-8")
    assert all(t in texto for t in ("fora do", "período", "da base"))  # spec §4
    assert "sem" not in re.findall(r">([^<]*)</text>", texto)


@pytest.fixture(scope="module")
def completo(tmp_path_factory):
    """Uma renderização real (matplotlib), compartilhada pelos testes que olham o resultado."""
    evidencias = evidencias_completas()
    auditoria = Auditoria()
    auditoria.iniciar_execucao(
        provedor="p", modelo="m", parametros={}, versao_codigo="abc", hash_prompts="h"
    )
    auditoria.registrar(
        "ferramentas",
        "ferramenta",
        ferramenta="consultar_metricas",
        argumentos={"segredo": "ARGUMENTO-CRU-123"},
        motivo="motivo visível",
    )
    dados = montar_dados(
        evidencias=evidencias,
        id_execucao=auditoria.id_execucao,
        status="ok",
        uf="BR",
        data_execucao=HOJE,
        texto=texto_valido(),
        avisos=[],
        violacoes=[],
        auditoria=auditoria,
        provedor="p",
        modelo="m",
        tokens_entrada=1000,
        tokens_saida=200,
        custo_usd=0.00072,
    )
    return montar_relatorio(dados, auditoria, tmp_path_factory.mktemp("relatorio"))


def test_os_dois_graficos_saem_em_svg_nos_dois_temas_e_em_png(completo):
    html = completo.html.read_text(encoding="utf-8")
    assert html.count("<svg") == 4
    for tipo in ("diaria_30d", "mensal_12m"):
        png = completo.pasta / "graficos" / f"{tipo}.png"
        assert png.read_bytes().startswith(b"\x89PNG")
        assert f"graficos/{tipo}.png" in completo.markdown.read_text(encoding="utf-8")


def test_svgs_embutidos_nao_repetem_ids_e_tem_rotulo(completo):
    html = completo.html.read_text(encoding="utf-8")
    ids = re.findall(r'\bid="([^"]+)"', html)
    repetidos = {i for i in ids if ids.count(i) > 1}
    assert not repetidos, sorted(repetidos)[:5]
    referencias = set(re.findall(r"url\(#([^)]+)\)", html)) | set(
        re.findall(r'href="#([^"]+)"', html)
    )
    assert referencias <= set(ids)  # referência não ficou apontando para id que mudou
    assert html.count('role="img"') == 4 and html.count("aria-label=") >= 4


def test_hash_da_csp_confere_com_o_script_da_pagina(completo):
    html = completo.html.read_text(encoding="utf-8")
    politica = htmllib.unescape(
        re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html).group(1)
    )
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    hash_ = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
    assert f"script-src 'sha256-{hash_}'" in politica


def test_anexo_nao_expoe_argumentos_crus_e_custo_e_em_pt_br(completo):
    html = completo.html.read_text(encoding="utf-8")
    markdown = completo.markdown.read_text(encoding="utf-8")
    assert "ARGUMENTO-CRU-123" not in html and "ARGUMENTO-CRU-123" not in markdown
    assert "motivo visível" in html
    assert "US$ 0,0007" in html and "US$ 0,0007" in markdown
    assert "0.0007" not in html and "0.0007" not in markdown


def test_faixa_de_revisao_humana(tmp_path, evidencias, graficos_falsos):
    arquivos = _montar(
        tmp_path,
        evidencias,
        status="revisao_humana",
        violacoes=["numero_nao_conferido: '50,0%' em resumo_executivo"],
    )
    html = arquivos.html.read_text(encoding="utf-8")
    assert "Revisão humana necessária" in html and "50,0%" in html


def test_relatorio_sem_texto_ainda_sai_com_numeros(tmp_path, evidencias, graficos_falsos):
    arquivos = _montar(
        tmp_path,
        evidencias,
        texto=None,
        status="revisao_humana",
        violacoes=["saida_invalida: json inválido"],
    )
    assert "10,0%" in arquivos.html.read_text(encoding="utf-8")
    assert "10,0%" in arquivos.markdown.read_text(encoding="utf-8")


def _com_supressao(evidencias):
    """Metrica suprimida (valor nulo), série com '<5' e 'oculto' e distribuição com 'oculto'."""
    for e in evidencias:
        if e.ferramenta == "consultar_metricas" and e.dados["chave"] == "mortalidade":
            e.dados.update(valor=None, numerador="<5", denominador=800, suprimido=True)
        if e.ferramenta == "consultar_serie" and e.dados["tipo"] == "diaria_30d":
            e.dados["pontos"][0]["casos"] = "<5"
            e.dados["pontos"][1]["casos"] = "oculto"
        if e.ferramenta == "consultar_distribuicao":
            e.dados["categorias"][0].update(casos="<5", percentual=None)
            e.dados["categorias"][1].update(casos="oculto", percentual=None)
    return evidencias


def test_supressao_aparece_no_html_e_no_markdown_com_as_notas(tmp_path, evidencias):
    arquivos = _montar(tmp_path, _com_supressao(evidencias), texto=None, status="revisao_humana")
    nota_serie = _serie(evidencias, "diaria_30d")["nota"]
    nota_dist = next(
        e.dados["nota"] for e in evidencias if e.ferramenta == "consultar_distribuicao"
    )
    html = arquivos.html.read_text(encoding="utf-8")
    markdown = arquivos.markdown.read_text(encoding="utf-8")
    for saida in (html, markdown):
        assert "oculto" in saida
    # pct(None) vira traço, nunca "0,0%"; a nota da série e a da distribuição são mostradas
    assert '<div class="valor">—</div>' in html and not re.search(r"(?<![\d,])0,0%", html)
    assert "&lt;5" in html and "Menos de 5 casos (&lt;5)" in html and "<5 de 800" in markdown
    assert "| — |" in markdown and "| <5 |" in markdown and "| oculto |" in markdown
    assert nota_serie in htmllib.unescape(html) and nota_dist in htmllib.unescape(html)
    assert nota_serie in markdown and nota_dist in markdown
    assert "Oculto (supressão complementar)" in html
