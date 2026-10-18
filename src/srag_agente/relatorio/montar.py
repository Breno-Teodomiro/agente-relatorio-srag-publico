"""Monta o relatório final: HTML autocontido, Markdown com PNGs e auditoria.jsonl."""

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape

from srag_agente.auditoria.registro import Auditoria
from srag_agente.ferramentas.registro import Evidencia
from srag_agente.guardrails.conteudo_externo import url_segura
from srag_agente.metricas.catalogo import CATALOGO
from srag_agente.modelos import NoticiaCitavel, TextoRelatorio, numerar_noticias
from srag_agente.relatorio.formatacao import data_br, inteiro, mes_br, pct, usd, variacao
from srag_agente.relatorio.graficos import grafico_diario, grafico_mensal
from srag_agente.relatorio.markdown import md_texto, md_url

AVISO_FIXO = (
    "Prova de conceito. Não substitui avaliação clínica nem boletim epidemiológico oficial."
)
ROTULOS_STATUS = {
    "ok": "concluído",
    "parcial": "parcial",
    "revisao_humana": "revisão humana necessária",
    "erro": "erro",
}
NOMES_CATEGORIAS = {
    "influenza": "Influenza",
    "covid19": "COVID-19",
    "outro_virus": "Outro vírus respiratório",
    "outro_agente": "Outro agente",
    "nao_especificado": "Não especificado",
    "em_investigacao": "Em investigação",
    "ignorado": "Ignorado",
}
GRAFICOS = (("diaria_30d", grafico_diario), ("mensal_12m", grafico_mensal))

# O único script da página (alternador de tema). A política de conteúdo (CSP) autoriza só o hash
# dele: texto que escape do escape e vire <script> não executa, e nada carrega de fora.
SCRIPT_TEMA = """
(function () {
  var raiz = document.documentElement;
  try { var salvo = localStorage.getItem("srag-tema"); if (salvo) raiz.dataset.tema = salvo; }
  catch (e) {}
  document.getElementById("tema").addEventListener("click", function () {
    raiz.dataset.tema = raiz.dataset.tema === "escuro" ? "claro" : "escuro";
    try { localStorage.setItem("srag-tema", raiz.dataset.tema); } catch (e) {}
  });
})();
"""
POLITICA_CONTEUDO = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{hash}'; "
    "base-uri 'none'; form-action 'none'"
)


@dataclass(frozen=True)
class DadosRelatorio:
    id_execucao: UUID
    status: str
    uf: str
    data_execucao: date
    data_referencia: date | None
    metricas: list[dict]
    series: dict[str, dict]
    distribuicoes: list[dict]
    noticias: list[NoticiaCitavel]
    texto: TextoRelatorio | None
    avisos: list[str]
    violacoes: list[str]
    eventos: list[dict]
    provedor: str
    modelo: str
    tokens_entrada: int
    tokens_saida: int
    custo_usd: float
    versao_codigo: str
    hash_prompts: str
    pergunta_extra: str | None = None


@dataclass(frozen=True)
class ArquivosRelatorio:
    pasta: Path
    html: Path
    markdown: Path
    auditoria: Path


def montar_dados(
    *,
    evidencias: list[Evidencia],
    id_execucao: UUID,
    status: str,
    uf: str,
    data_execucao: date,
    texto: TextoRelatorio | None,
    avisos: list[str],
    violacoes: list[str],
    auditoria: Auditoria,
    provedor: str,
    modelo: str,
    tokens_entrada: int,
    tokens_saida: int,
    custo_usd: float,
    pergunta_extra: str | None = None,
) -> DadosRelatorio:
    def dados(ferramenta: str) -> list[dict]:
        return [
            e.dados
            for e in evidencias
            if e.ferramenta == ferramenta and e.dados.get("uf", uf) == uf
        ]

    por_chave = {m["chave"]: m for m in dados("consultar_metricas")}
    series = {s["tipo"]: s for s in dados("consultar_serie")}
    data_referencia = next(
        (date.fromisoformat(s["data_referencia"]) for s in series.values()), None
    )
    return DadosRelatorio(
        id_execucao=id_execucao,
        status=status,
        uf=uf,
        data_execucao=data_execucao,
        data_referencia=data_referencia,
        metricas=[por_chave[chave] for chave in CATALOGO if chave in por_chave],
        series=series,
        distribuicoes=dados("consultar_distribuicao"),
        noticias=numerar_noticias(evidencias),
        texto=texto,
        avisos=list(avisos),
        violacoes=list(violacoes),
        eventos=list(auditoria.eventos),
        provedor=provedor,
        modelo=modelo,
        tokens_entrada=tokens_entrada,
        tokens_saida=tokens_saida,
        custo_usd=custo_usd,
        versao_codigo=auditoria.execucao.get("versao_codigo", ""),
        hash_prompts=auditoria.execucao.get("hash_prompts", ""),
        pergunta_extra=pergunta_extra,
    )


def _citacoes(texto: str | None) -> Markup:
    """Escapa o texto do LLM e só então transforma [n] em link para a referência."""
    if not texto:
        return Markup("")
    return Markup(re.sub(r"\[(\d+)\]", r'<a href="#ref-\1">[\1]</a>', str(escape(texto))))


def _url_segura(url: str) -> str:
    """Link que não é http(s) com host vira "#": o template nunca emite javascript:/data:."""
    return url if url_segura(url) else "#"


def _ambiente() -> Environment:
    ambiente = Environment(
        loader=PackageLoader("srag_agente.relatorio", "templates"),
        autoescape=select_autoescape(enabled_extensions=("html.j2",), default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    ambiente.filters.update(
        pct=pct,
        variacao=variacao,
        inteiro=inteiro,
        data_br=data_br,
        mes_br=mes_br,
        usd=usd,
        citacoes=_citacoes,
        url_segura=_url_segura,
        md=md_texto,
        md_url=md_url,
        categoria=lambda c: NOMES_CATEGORIAS.get(c, c),
    )
    return ambiente


def _svg(conteudo: bytes) -> Markup:
    texto = conteudo.decode("utf-8")
    return Markup(texto[texto.index("<svg") :])  # sem a declaração XML, para embutir no HTML


def _hash_script(script: str) -> str:
    return base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")


def montar_relatorio(
    dados: DadosRelatorio, auditoria: Auditoria, pasta_base: Path
) -> ArquivosRelatorio:
    """Gráficos, HTML, Markdown e a trilha, de uma vez (trilha já encerrada)."""
    arquivos = escrever_relatorio(dados, pasta_base)
    auditoria.gravar_jsonl(arquivos.auditoria)
    return arquivos


def escrever_relatorio(dados: DadosRelatorio, pasta_base: Path) -> ArquivosRelatorio:
    """Gráficos, HTML e Markdown. Não grava a trilha: `auditoria` é só o destino dela.

    O núcleo grava o relatório antes de encerrar a execução e a trilha depois: assim a execução
    só fica "ok" com o relatório no disco, e o arquivo da trilha traz o encerramento.
    """
    pasta = pasta_base / f"{dados.data_execucao:%Y-%m-%d}_{dados.uf}_{str(dados.id_execucao)[:8]}"
    (pasta / "graficos").mkdir(parents=True, exist_ok=True)
    graficos: dict[str, dict[str, Markup]] = {}
    for tipo, desenhar in GRAFICOS:
        if (serie := dados.series.get(tipo)) is None:
            continue
        graficos[tipo] = {tema: _svg(desenhar(serie, tema)) for tema in ("escuro", "claro")}
        (pasta / "graficos" / f"{tipo}.png").write_bytes(desenhar(serie, "claro", "png"))
    contexto = {
        "d": dados,
        "graficos": graficos,
        "aviso": AVISO_FIXO,
        "rotulo_status": ROTULOS_STATUS.get(dados.status, dados.status),
        "comentarios": (
            {c.metrica: c.comentario for c in dados.texto.comentarios_metricas}
            if dados.texto
            else {}
        ),
        "script_tema": Markup(SCRIPT_TEMA),
        "politica": POLITICA_CONTEUDO.format(hash=_hash_script(SCRIPT_TEMA)),
    }
    ambiente = _ambiente()
    html = pasta / "relatorio.html"
    html.write_text(ambiente.get_template("relatorio.html.j2").render(**contexto), encoding="utf-8")
    markdown = pasta / "relatorio.md"
    markdown.write_text(
        ambiente.get_template("relatorio.md.j2").render(**contexto), encoding="utf-8"
    )
    return ArquivosRelatorio(
        pasta=pasta,
        html=html,
        markdown=markdown,
        auditoria=pasta / "auditoria.jsonl",
    )
