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
from srag_agente.relatorio.graficos import TEMAS, _altura, grafico_diario, grafico_mensal
from srag_agente.relatorio.markdown import md_texto
from srag_agente.relatorio.montar import (
    AVISO_FIXO,
    AVISO_RETENCAO,
    escrever_relatorio,
    montar_dados,
)
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


def _montar_relatorio(dados, auditoria, pasta):
    """Como o núcleo: escreve o relatório e depois a trilha."""
    arquivos = escrever_relatorio(dados, pasta)
    auditoria.gravar_jsonl(arquivos.auditoria)
    return arquivos


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
    return _montar_relatorio(montar_dados(**campos), auditoria, tmp_path)


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
    return _montar_relatorio(replace(dados, **mudar), auditoria, tmp_path)


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
    return _montar_relatorio(dados, auditoria, tmp_path_factory.mktemp("relatorio"))


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


def test_revisao_humana_mostra_retencao_no_lugar_dos_comentarios(
    tmp_path, evidencias, graficos_falsos
):
    """O agente retém o texto reprovado (texto None): o relatório diz isso, com SQL e faixa."""
    violacao = "link_nao_verificado: https://falso.example.com/x em cenario_atual"
    arquivos = _montar(
        tmp_path, evidencias, texto=None, status="revisao_humana", violacoes=[violacao]
    )
    html = arquivos.html.read_text(encoding="utf-8")
    markdown = arquivos.markdown.read_text(encoding="utf-8")
    assert '<div class="faixa perigo"><strong>Revisão humana necessária.</strong>' in html
    assert "> **Revisão humana necessária.**" in markdown
    assert f"<p>{AVISO_RETENCAO}</p>" in html
    assert f"\n{AVISO_RETENCAO}\n" in markdown
    for saida in (html, markdown):
        assert "10,0%" in saida  # mortalidade do SQL
        assert "Resumo" not in saida and "Limitações" not in saida
    assert "falso.example.com/x" in markdown and "https://falso" not in markdown  # sem autolink


@pytest.mark.parametrize("status", ["ok", "parcial"])
def test_retencao_so_aparece_em_revisao_humana(tmp_path, evidencias, graficos_falsos, status):
    arquivos = _montar(tmp_path, evidencias, status=status)
    for saida in (arquivos.html, arquivos.markdown):
        conteudo = saida.read_text(encoding="utf-8")
        assert AVISO_RETENCAO not in conteudo and "Resumo" in conteudo
    # sem texto e fora da revisão humana (o grafo não produz isso): nada é dito como retido
    sem_texto = _montar(tmp_path, evidencias, status=status, texto=None)
    for saida in (sem_texto.html, sem_texto.markdown):
        assert AVISO_RETENCAO not in saida.read_text(encoding="utf-8")


def test_markdown_nao_transforma_endereco_em_autolink():
    """GFM transforma http(s):// e www. em link clicável; com o escape, ficam texto."""
    saida = md_texto("Veja https://a.example/x, HTTP://b.example e www.c.example/y.")
    assert not re.search(r"(?<!\\)://", saida) and "www." not in saida
    assert saida == "Veja https\\://a.example/x, HTTP\\://b.example e www\\.c.example/y."


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
    pagina = htmllib.unescape(html)
    corte = nota_serie.index("Contagens de 1 a 4")  # a nota longa fica recolhida em <details>
    assert nota_serie[:corte].strip() in pagina and nota_serie[corte:] in pagina
    assert nota_dist in pagina
    assert nota_serie in markdown and nota_dist in markdown
    assert "Oculto (supressão complementar)" in html


def _md_com_eventos(tmp_path, evidencias, graficos_falsos, eventos):
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
    arquivos = _montar_relatorio(replace(dados, eventos=eventos), auditoria, tmp_path)
    return arquivos.markdown.read_text(encoding="utf-8")


def _evento(sequencia, **campos):
    base = {
        "sequencia": sequencia,
        "no": "orquestrador",
        "tipo": "ferramenta",
        "ferramenta": None,
        "motivo": None,
        "resultado": None,
        "erro": None,
        "duracao_ms": 5,
    }
    return base | campos


def test_markdown_traz_o_anexo_com_a_trilha(tmp_path, evidencias, graficos_falsos):
    eventos = [
        _evento(1, ferramenta="consultar_metricas", motivo="preciso da mortalidade"),
        _evento(2, no="guardrail", tipo="guardrail", resultado={"aprovado": False}),
        _evento(3, tipo="erro", erro="falhou"),
    ]
    markdown = _md_com_eventos(tmp_path, evidencias, graficos_falsos, eventos)
    anexo = markdown[markdown.index("## Como este relatório foi gerado") :]
    assert "| # | Etapa | Tipo | Ferramenta | Motivo / resultado | ms |" in anexo
    assert (
        "| 1 | orquestrador | ferramenta | consultar_metricas | preciso da mortalidade | 5 |"
        in anexo
    )
    assert "— reprovado" in anexo and "**erro:** falhou" in anexo
    assert "auditoria.jsonl" in anexo and AVISO_FIXO in anexo


def test_texto_hostil_nao_quebra_a_tabela_de_eventos(tmp_path, evidencias, graficos_falsos):
    hostil = "a | b\n[x](http://evil) <script>\n| c \\| d"
    eventos = [_evento(1, ferramenta="f|g", motivo=hostil, erro=hostil)]
    markdown = _md_com_eventos(tmp_path, evidencias, graficos_falsos, eventos)
    linha = next(x for x in markdown.splitlines() if x.startswith("| 1 | orquestrador"))
    assert "<script>" not in linha and "\n" not in linha  # sem tag e em uma linha só
    assert "&lt;script&gt;" in linha and "\\[x\\]" in linha
    # as 6 células: toda barra vertical do texto está escapada, sobram só as 7 delimitadoras
    assert len(re.findall(r"(?<!\\)(?:\\\\)*\|", linha)) == 7


def test_markdown_diz_casos_contra_semana_anterior(tmp_path, evidencias, graficos_falsos):
    markdown = _montar(tmp_path, evidencias).markdown.read_text(encoding="utf-8")
    assert " casos contra " in markdown and " na semana anterior" in markdown
    linha = next(x for x in markdown.splitlines() if x.startswith("| Taxa de aumento"))
    assert " de " not in linha.split("|")[3]  # a Base não se lê como fração


def test_anexo_mostra_o_modelo_que_respondeu_no_markdown(tmp_path, evidencias, graficos_falsos):
    eventos = [_evento(1, tipo="llm", resultado={"modelo": "reserva|<b>x</b>"})]
    markdown = _md_com_eventos(tmp_path, evidencias, graficos_falsos, eventos)
    linha = next(x for x in markdown.splitlines() if x.startswith("| 1 | orquestrador"))
    assert "modelo: reserva\\|&lt;b&gt;x&lt;/b&gt;" in linha
    assert len(re.findall(r"(?<!\\)(?:\\\\)*\|", linha)) == 7


# ---- referências: só as notícias citadas, renumeradas (D2) -------------------------------------
def _com_mais_noticias(evidencias):
    """Quatro notícias numeradas 1..4: [1] e [3] e [4] do período, [2] do cenário atual."""
    periodo = next(e for e in evidencias if e.ferramenta == "buscar_noticias"
                   and e.dados["janela"] == "periodo_dos_dados")  # fmt: skip
    extra = deepcopy(periodo)
    modelo = extra.dados["noticias"][0]
    extra.dados["noticias"] = [
        {**modelo, "titulo": f"Boletim {n} de SRAG", "url": f"https://www.gov.br/saude/b{n}"}
        for n in (3, 4)
    ]
    return [*evidencias, extra]


def _refs_md(markdown):
    return [linha for linha in markdown.splitlines() if re.match(r"\d+\. \[", linha)]


def test_referencias_listam_so_as_citadas_renumeradas_na_ordem_de_leitura(
    tmp_path, evidencias, graficos_falsos
):
    texto = texto_valido(
        resumo_executivo="Alta nas internações [3].",
        contexto_periodo_dados="Boletins do período [1][3]; e também [ 4 ].",
        cenario_atual="Nenhuma notícia atual foi citada.",
    )
    arquivos = _montar(tmp_path, _com_mais_noticias(evidencias), texto=texto)
    markdown = arquivos.markdown.read_text(encoding="utf-8")
    refs = _refs_md(markdown)
    assert [r.split("](")[0] for r in refs] == [
        "1. [Boletim 3 de SRAG", "2. [Fiocruz aponta alta de SRAG por influenza",
        "3. [Boletim 4 de SRAG",
    ]  # fmt: skip
    assert "Ministério divulga boletim" not in markdown  # [2] não foi citada: fica fora
    assert "Alta nas internações [1]." in markdown
    assert "Boletins do período \\[2\\][1]; e também \\[ 3 \\]." in markdown
    html = arquivos.html.read_text(encoding="utf-8")
    assert re.findall(r'<li id="ref-(\d+)" value="(\d+)">([^—]+?) —', html) == [
        ("1", "1", "Boletim 3 de SRAG"), ("2", "2", "Fiocruz aponta alta de SRAG por influenza"),
        ("3", "3", "Boletim 4 de SRAG"),
    ]  # fmt: skip
    assert 'Alta nas internações <a href="#ref-1">[1]</a>' in html


def test_citacao_agrupada_e_de_largura_total_tambem_e_renumerada(
    tmp_path, evidencias, graficos_falsos
):
    texto = texto_valido(
        resumo_executivo="Ver [4, 1].",
        contexto_periodo_dados="Ver \uff3b4\uff3d e [1; 4].",
        cenario_atual="Sem notícia atual.",
    )
    dados = montar_dados(**{**_campos(evidencias), "evidencias": _com_mais_noticias(evidencias),
                            "texto": texto})  # fmt: skip
    assert dados.texto.resumo_executivo == "Ver [1, 2]."
    assert dados.texto.contexto_periodo_dados == "Ver [1] e [2; 1]."
    assert [(n.id, n.url) for n in dados.noticias] == [
        (1, "https://www.gov.br/saude/b4"), (2, "https://portal.fiocruz.br/n1"),
    ]  # fmt: skip


def test_sem_texto_aprovado_nenhuma_referencia_e_listada(tmp_path, evidencias, graficos_falsos):
    arquivos = _montar(tmp_path, evidencias, texto=None, status="revisao_humana",
                       violacoes=["numero_nao_conferido: '9%' em resumo_executivo"])  # fmt: skip
    for saida in (arquivos.markdown, arquivos.html):
        conteudo = saida.read_text(encoding="utf-8")
        assert "Nenhuma das 2 notícias encontradas nas buscas foi citada no texto" in conteudo
        assert "Fiocruz aponta alta" not in conteudo


def test_sem_noticia_nas_buscas_a_mensagem_continua_a_mesma(tmp_path, evidencias, graficos_falsos):
    sem = [e for e in evidencias if e.ferramenta != "buscar_noticias"]
    texto = texto_valido(contexto_periodo_dados="Sem notícias.", cenario_atual="Sem notícias.")
    markdown = _montar(tmp_path, sem, texto=texto).markdown.read_text(encoding="utf-8")
    assert "Nenhuma notícia foi encontrada nas buscas desta execução." in markdown


# ---- revisão final: aviso com [n] segue a numeração das referências (M2) ----------------------
_AVISO_SEM_DATA = ("noticia_sem_data: [{n}] citada em cenario_atual; a janela não pôde ser "
                   "conferida pela data")  # fmt: skip


def test_aviso_de_noticia_sem_data_usa_o_numero_da_referencia(evidencias):
    """Ordem de leitura: [3] (resumo) vira 1, [1] vira 2 e [2] (cenário atual) vira 3."""
    texto = texto_valido(resumo_executivo="Alta nas internações [3].",
                         contexto_periodo_dados="Boletins do período [1][3].",
                         cenario_atual="O Ministério divulgou boletim [2].")  # fmt: skip
    outro = "Evidências incompletas: faltou consultar_serie [9]."  # aviso nosso: não muda
    dados = montar_dados(**{**_campos(evidencias), "evidencias": _com_mais_noticias(evidencias),
                            "texto": texto,
                            "avisos": [outro, _AVISO_SEM_DATA.format(n=2)]})  # fmt: skip
    assert [n.url for n in dados.noticias][2] == "https://www.gov.br/saude/n2"
    assert dados.avisos == [outro, _AVISO_SEM_DATA.format(n=3)]


def test_aviso_sem_referencia_aponta_para_a_trilha_e_nao_para_um_numero(
    tmp_path, evidencias, graficos_falsos
):
    """Revisão humana: texto retido, nenhuma referência. "[2]" apontaria para o nada."""
    arquivos = _montar(tmp_path, evidencias, texto=None, status="revisao_humana",
                       violacoes=["numero_nao_conferido: '9%' em resumo_executivo"],
                       avisos=[_AVISO_SEM_DATA.format(n=2)])  # fmt: skip
    markdown = arquivos.markdown.read_text(encoding="utf-8")
    html = htmllib.unescape(arquivos.html.read_text(encoding="utf-8"))
    for conteudo in (markdown, html):
        assert "noticia_sem_data: notícia 2 da trilha de auditoria citada em" in conteudo
        assert "[2]" not in conteudo and "\\[2\\]" not in conteudo


def test_aviso_de_noticia_citada_so_pelo_link_nao_vira_numero_de_referencia(evidencias):
    """Notícia citada só pela URL não entra nas referências: o aviso dela aponta para a trilha."""
    texto = texto_valido(cenario_atual="Ver https://www.gov.br/saude/n2 para o boletim.")
    dados = montar_dados(**{**_campos(evidencias), "texto": texto,
                            "avisos": [_AVISO_SEM_DATA.format(n=2)]})  # fmt: skip
    assert [n.id for n in dados.noticias] == [1]  # só [1], do contexto do período
    assert dados.avisos == [_AVISO_SEM_DATA.format(n=2).replace(
        "[2]", "notícia 2 da trilha de auditoria")]  # fmt: skip


# ---- revisão final: o anexo diz a verdade sobre a trilha (I3) ---------------------------------
def test_anexo_nao_promete_trilha_completa_e_diz_onde_ficam_as_citadas(
    tmp_path, evidencias, graficos_falsos
):
    """A trilha trunca resultados longos (2.000 caracteres): "completa" seria falso."""
    arquivos = _montar(tmp_path, evidencias)
    sem_citacao = _montar(tmp_path / "b", evidencias, texto=None, status="revisao_humana")
    for caminho in (arquivos.markdown, arquivos.html, sem_citacao.markdown, sem_citacao.html):
        conteudo = re.sub(r"\s+", " ", htmllib.unescape(caminho.read_text(encoding="utf-8")))
        assert "trilha completa" not in conteudo and "(estão na trilha" not in conteudo
        assert "truncados" in conteudo and "guardrail_saida" in conteudo


def _campos(evidencias):
    auditoria = Auditoria()
    auditoria.iniciar_execucao(provedor="p", modelo="m", parametros={}, versao_codigo="abc",
                               hash_prompts="h")  # fmt: skip
    return dict(
        evidencias=evidencias, id_execucao=auditoria.id_execucao, status="ok", uf="BR",
        data_execucao=HOJE, texto=texto_valido(), avisos=[], violacoes=[], auditoria=auditoria,
        provedor="p", modelo="m", tokens_entrada=1, tokens_saida=1, custo_usd=0.0,
    )  # fmt: skip


# ---- todo número do texto aparece no relatório (D1: o 28,6% não estava em tabela nenhuma) -----
def test_apoio_de_30_dias_e_vacina_da_gripe_aparecem_nos_dois_formatos(
    tmp_path, evidencias, graficos_falsos
):
    arquivos = _montar(tmp_path, evidencias)
    markdown = arquivos.markdown.read_text(encoding="utf-8")
    linha = next(x for x in markdown.splitlines() if x.startswith("| Taxa de aumento"))
    assert "30 dias consolidados: 300 contra 250 nos 30 anteriores (+20,0%)" in linha
    vacina = next(x for x in markdown.splitlines() if x.startswith("| Taxa de vacinação"))
    assert "gripe: 32,1% (250 de 780)" in vacina
    html = arquivos.html.read_text(encoding="utf-8")
    assert "30 dias consolidados: 300 contra 250 nos 30 anteriores (+20,0%)" in html


def test_apoio_de_30_dias_suprimido_sai_como_rotulo(tmp_path, evidencias, graficos_falsos):
    for e in evidencias:
        if e.dados.get("chave") == "aumento_casos":
            e.dados["complemento"] = {"casos_30d_atual": "<5", "casos_30d_anterior": 7,
                                      "variacao_30d": None}  # fmt: skip
    markdown = _montar(tmp_path, evidencias).markdown.read_text(encoding="utf-8")
    assert "30 dias consolidados: <5 contra 7 nos 30 anteriores (—)" in markdown.replace(
        "&lt;", "<"
    )


def test_graficos_nao_dependem_do_matplotlibrc_do_usuario(tmp_path):
    """18/10/2026: um matplotlibrc do usuário com backend inválido avisava a cada execução."""
    import os
    import subprocess
    import sys

    configuracao = tmp_path / "config"
    configuracao.mkdir()
    (configuracao / "matplotlibrc").write_text(
        "backend: module://matplotlib_inline.backend_inline\naxes.facecolor: red\n"
    )
    ambiente = {k: v for k, v in os.environ.items() if k not in ("MATPLOTLIBRC", "MPLBACKEND")}
    ambiente["MPLCONFIGDIR"] = str(configuracao)
    codigo = (
        "import srag_agente.relatorio.graficos, matplotlib; "
        "print(matplotlib.get_backend(), matplotlib.rcParams['axes.facecolor'])"
    )
    feito = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                           env=ambiente, cwd=tmp_path, timeout=120, check=True)  # fmt: skip
    assert "Bad value" not in feito.stderr and "matplotlibrc" not in feito.stderr, feito.stderr
    backend, fundo = feito.stdout.split()
    assert (backend.lower(), fundo) == ("agg", "white")  # o rc do usuário não muda o gráfico


def test_ordem_de_leitura_poe_os_comentarios_antes_do_contexto(evidencias):
    comentarios = [*texto_valido().comentarios_metricas[1:],
                   {"metrica": "aumento_casos", "comentario": "Alta citada [4]."}]  # fmt: skip
    texto = texto_valido(
        resumo_executivo="Sem citação.",
        comentarios_metricas=comentarios,
        contexto_periodo_dados="Boletim [1].",
        cenario_atual="Sem citação.",
    )
    dados = montar_dados(**{**_campos(evidencias), "evidencias": _com_mais_noticias(evidencias),
                            "texto": texto})  # fmt: skip
    assert [(n.id, n.url) for n in dados.noticias] == [
        (1, "https://www.gov.br/saude/b4"), (2, "https://portal.fiocruz.br/n1"),
    ]  # fmt: skip
    assert dados.texto.contexto_periodo_dados == "Boletim [2]."


def test_citacao_de_noticia_inexistente_nao_herda_numero_de_outra(evidencias):
    texto = texto_valido(resumo_executivo="Ver [9] e [1].", cenario_atual="Sem citação.",
                         contexto_periodo_dados="Sem citação.")  # fmt: skip
    dados = montar_dados(**{**_campos(evidencias), "texto": texto})
    assert dados.texto.resumo_executivo == "Ver [?] e [1]."
    assert [n.id for n in dados.noticias] == [1]


def _luminancia(cor: str) -> float:
    canais = [int(cor.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _razao(a: str, b: str) -> float:
    claro, escuro = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (claro + 0.05) / (escuro + 0.05)


@pytest.mark.parametrize("tema", ["escuro", "claro"])
def test_barras_dos_graficos_tem_contraste_minimo_com_o_fundo(tema):
    cores = TEMAS[tema]
    assert _razao(cores["barra"], cores["fundo"]) >= 3  # WCAG 1.4.11: gráficos
    assert _razao(cores["clara"], cores["fundo"]) >= 3  # "em consolidação" também


@pytest.mark.parametrize("desenhar", [grafico_diario, grafico_mensal])
def test_svg_nasce_estreito_para_o_texto_nao_encolher_no_celular(evidencias, desenhar):
    tipo = "diaria_30d" if desenhar is grafico_diario else "mensal_12m"
    svg = desenhar(_serie(evidencias, tipo), "escuro").decode("utf-8")
    largura_pt = float(re.search(r'<svg[^>]* width="([\d.]+)pt"', svg).group(1))
    assert largura_pt <= 450  # com ~545 pt o texto encolhia a 0,6 no celular (390 px)


def test_html_acessivel_nas_tabelas_botao_e_links(completo):
    html = completo.html.read_text(encoding="utf-8")
    assert "<th>" not in html and html.count('<th scope="col">') >= 9
    assert re.search(r"<caption[^>]*>\s*Trilha de auditoria", html)
    assert 'id="tema"' in html and 'aria-pressed="' in html
    assert ":focus-visible{outline:2px solid var(--acento)" in html
    assert '<span class="sr-only"> (abre em nova aba)</span>' in html


def test_html_recolhe_a_nota_longa_de_supressao_e_ajusta_o_celular(completo):
    html = completo.html.read_text(encoding="utf-8")
    assert "Como ler as células" in html and "<details" in html
    assert "min-width:640px" in html and "figure svg{display:block;margin:0 auto" in html
