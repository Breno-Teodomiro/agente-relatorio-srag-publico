"""Busca de notícias em tempo real: Tavily (principal) com Google News RSS de reserva.

A reserva é decidida DEPOIS dos filtros de janela, injeção e relevância: um Tavily que responde
mas não deixa nenhuma notícia aproveitável também aciona o RSS. Resposta inválida, grande demais ou
item malformado não derrubam a busca; viram texto em ``erros``, sem a chave da API.

Relevância (conservadora e declarada): título ou trecho precisa falar do tema ("síndrome
respiratória" ou a sigla SRAG como palavra) E de saúde pública (``_SAUDE``), e o domínio da
notícia e o da fonte não podem ser de rede social nem de mercado financeiro
(``DOMINIOS_BLOQUEADOS``). A primeira execução real trouxe notícias de bolsa de uma empresa com
ticker "XETR:SRAG", um reel, um blog de esporte e um comitê científico britânico de mesma sigla.
Limitação: é lista de termos, não classificador; notícia de outro país que fale de SRAG e de
"casos" passa (a consulta já pede Brasil).
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import urlparse

import feedparser
import httpx
from pydantic import BaseModel

from srag_agente.guardrails.conteudo_externo import (
    dominio_confiavel,
    sanitizar_texto_externo,
    tem_dado_pessoal,
    tem_injecao,
    url_segura,
)
from srag_agente.guardrails.padroes import normalizar_unicode

Janela = Literal["periodo_dos_dados", "cenario_atual"]
Provedor = Literal["tavily", "google_news_rss", "nenhum"]
URL_TAVILY = "https://api.tavily.com/search"
URL_GOOGLE_NEWS = "https://news.google.com/rss/search"
TIMEOUT_S = 20
LIMITE_RESPOSTA_BYTES = 2_000_000
LIMITE_MENSAGEM_ERRO = 300
TEMA_CONSULTA = "síndrome respiratória aguda grave"
PAIS_CONSULTA = "Brasil"
DOMINIOS_BLOQUEADOS: tuple[str, ...] = (
    # redes sociais e vídeo
    "instagram.com", "facebook.com", "x.com", "twitter.com", "youtube.com", "tiktok.com",
    # mercado financeiro (SRAG também é ticker de bolsa)
    "tradingview.com", "investing.com", "marketscreener.com",
)  # fmt: skip
_TEMA = re.compile(r"s[ií]ndrome\s+respirat[óo]ria|(?<![\w-])srag(?![\w-])", re.IGNORECASE)
_SAUDE = re.compile(
    r"sa[úu]de|hospita|interna[çc]|vacin|\bcasos\b|secretaria|minist[ée]rio|fiocruz|boletim"
    r"|influenza|covid|v[íi]rus|gripe|vigil[âa]ncia|epidemiol[óo]gic|notifica[çc]|[óo]bitos?\b",
    re.IGNORECASE,
)


class Noticia(BaseModel):
    titulo: str
    fonte: str
    url: str
    data: date | None
    trecho: str
    confiavel: bool
    janela: Janela


class ResultadoBusca(BaseModel):
    termo: str
    janela: Janela
    inicio: date
    fim: date
    provedor: Provedor
    noticias: list[Noticia]
    descartadas_por_injecao: int = 0
    descartadas_por_irrelevancia: int = 0
    erros: list[str] = []


class RespostaInvalida(ValueError):  # noqa: N818 (nome em português, como DadosIndisponiveis)
    """Resposta do provedor fora do formato esperado ou acima do limite de tamanho."""


@dataclass(frozen=True)
class _ItemBruto:
    titulo: str
    url: str
    url_fonte: str
    fonte: str
    data: date | None
    texto: str


def _texto(valor: object) -> str | None:
    return valor if isinstance(valor, str) else None


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def _data(valor: object) -> date | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        return date.fromisoformat(valor[:10])
    except (TypeError, ValueError):
        pass
    try:
        return parsedate_to_datetime(valor).date()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _item_tavily(bruto: object) -> _ItemBruto | None:
    if not isinstance(bruto, dict):
        return None
    titulo, url = _texto(bruto.get("title")), _texto(bruto.get("url"))
    conteudo = bruto.get("content") or ""
    if titulo is None or not url or not isinstance(conteudo, str):
        return None
    return _ItemBruto(
        titulo=titulo,
        url=url,
        url_fonte=url,
        fonte=_host(url),
        data=_data(bruto.get("published_date")),
        texto=conteudo,
    )


def _item_rss(entrada: dict) -> _ItemBruto | None:
    link = _texto(entrada.get("link"))
    if not link:
        return None
    fonte = entrada.get("source")
    fonte = fonte if isinstance(fonte, dict) else {}
    publicado = entrada.get("published_parsed")
    try:
        data = date(*publicado[:3]) if publicado else None
    except (TypeError, ValueError):
        data = None
    return _ItemBruto(
        titulo=_texto(entrada.get("title")) or "",
        url=link,
        url_fonte=_texto(fonte.get("href")) or link,
        fonte=_texto(fonte.get("title")) or "",
        data=data,
        texto=_texto(entrada.get("summary")) or "",
    )


def _dominio_bloqueado(url: str) -> bool:
    host = _host(url).lower()
    return any(host == d or host.endswith("." + d) for d in DOMINIOS_BLOQUEADOS)


def noticia_relevante(titulo: str, trecho: str, *urls: str) -> bool:
    """Fala de SRAG e de saúde pública, e nenhum dos ``urls`` é de rede social ou de finanças."""
    texto = normalizar_unicode(f"{titulo} {trecho}")
    return (
        bool(_TEMA.search(texto))
        and bool(_SAUDE.search(texto))
        and not any(_dominio_bloqueado(u) for u in urls if u)
    )


def consulta_noticias(termo: str) -> str:
    """O termo do orquestrador com o tema e o país, quando ele não os traz."""
    sem_acento = normalizar_unicode(termo).casefold()
    consulta = termo
    if not re.search(r"s[ií]ndrome\s+respirat[óo]ria\s+aguda\s+grave", sem_acento):
        consulta += f" {TEMA_CONSULTA}"
    if not re.search(r"\bbrasil\b", sem_acento):
        consulta += f" {PAIS_CONSULTA}"
    return consulta


def _registrar_malformados(erros: list[str], provedor: str, quantidade: int) -> None:
    if quantidade:
        erros.append(f"{provedor}: {quantidade} item(ns) malformado(s) ignorado(s)")


class BuscadorNoticias:
    def __init__(self, http: httpx.Client, tavily_api_key: str | None, max_resultados: int = 5):
        self._http = http
        self._tavily_api_key = tavily_api_key
        self._max = max_resultados

    def _baixar(self, metodo: str, url: str, **kwargs) -> bytes:
        """Baixa o corpo em pedaços e para assim que passa do limite (barra resposta gigante)."""
        with self._http.stream(metodo, url, timeout=TIMEOUT_S, **kwargs) as resposta:
            resposta.raise_for_status()
            corpo = bytearray()
            for pedaco in resposta.iter_bytes():
                corpo += pedaco
                if len(corpo) > LIMITE_RESPOSTA_BYTES:
                    raise RespostaInvalida(
                        f"resposta acima do limite de {LIMITE_RESPOSTA_BYTES} bytes"
                    )
        return bytes(corpo)

    def _tavily(self, termo: str, inicio: date, fim: date, erros: list[str]) -> list[_ItemBruto]:
        corpo = self._baixar(
            "POST",
            URL_TAVILY,
            json={
                "query": consulta_noticias(termo),
                "topic": "news",
                "start_date": inicio.isoformat(),
                "end_date": fim.isoformat(),
                "max_results": self._max * 2,
                "search_depth": "basic",
            },
            headers={"Authorization": f"Bearer {self._tavily_api_key}"},
        )
        dados = json.loads(corpo)
        resultados = dados.get("results") if isinstance(dados, dict) else None
        if not isinstance(resultados, list):
            raise RespostaInvalida("formato inesperado: falta a lista 'results'")
        itens = [_item_tavily(bruto) for bruto in resultados]
        _registrar_malformados(erros, "tavily", itens.count(None))
        return [item for item in itens if item is not None]

    def _rss(self, termo: str, inicio: date, fim: date, erros: list[str]) -> list[_ItemBruto]:
        consulta = (
            f"{consulta_noticias(termo)} after:{inicio.isoformat()} "
            f"before:{(fim + timedelta(days=1)).isoformat()}"
        )
        corpo = self._baixar(
            "GET",
            URL_GOOGLE_NEWS,
            params={"q": consulta, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"},
        )
        feed = feedparser.parse(corpo)
        if feed.get("bozo"):
            motivo = type(feed.get("bozo_exception")).__name__
            erros.append(f"google_news_rss: feed malformado ({motivo}); itens legíveis mantidos")
        itens = [_item_rss(entrada) for entrada in feed.entries]
        _registrar_malformados(erros, "google_news_rss", itens.count(None))
        return [item for item in itens if item is not None]

    def _descrever_erro(self, provedor: str, erro: Exception) -> str:
        texto = " ".join(f"{provedor}: {type(erro).__name__}: {erro}".split())
        if self._tavily_api_key:
            texto = texto.replace(self._tavily_api_key, "***")
        return texto[:LIMITE_MENSAGEM_ERRO]

    def _filtrar(
        self,
        itens: list[_ItemBruto],
        janela: Janela,
        inicio: date,
        fim: date,
        provedor: str,
        erros: list[str],
    ) -> tuple[list[Noticia], int, int]:
        """Devolve (notícias, descartadas por injeção, descartadas por irrelevância)."""
        noticias: list[Noticia] = []
        descartadas = irrelevantes = sem_conteudo = 0
        for item in itens:
            if item.data is not None and not inicio <= item.data <= fim:
                continue
            if not url_segura(item.url) or any(
                tem_injecao(campo) for campo in (item.titulo, item.texto, item.fonte)
            ):
                descartadas += 1
                continue
            titulo = sanitizar_texto_externo(item.titulo, limite=200)
            trecho = sanitizar_texto_externo(item.texto)
            if titulo is None or trecho is None:
                sem_conteudo += 1
                continue
            if not noticia_relevante(titulo, trecho, item.url, item.url_fonte):
                irrelevantes += 1
                continue
            noticias.append(
                Noticia(
                    titulo=titulo,
                    fonte=sanitizar_texto_externo(item.fonte, limite=80) or "",
                    url=item.url,
                    data=item.data,
                    trecho=trecho,
                    confiavel=dominio_confiavel(item.url_fonte),
                    janela=janela,
                )
            )
        if sem_conteudo:
            erros.append(f"{provedor}: {sem_conteudo} item(ns) sem título ou trecho ignorado(s)")
        return noticias, descartadas, irrelevantes

    def buscar(self, termo: str, janela: Janela, inicio: date, fim: date) -> ResultadoBusca:
        if tem_injecao(termo) or tem_dado_pessoal(termo):
            # Segunda barreira (a primeira é a validação dos argumentos): nada sai para fora.
            return ResultadoBusca(
                termo="[omitido]", janela=janela, inicio=inicio, fim=fim, provedor="nenhum",
                noticias=[],
                erros=["termo recusado: dado pessoal ou instrução ao modelo; busca não feita"],
            )  # fmt: skip
        coletores: list[tuple[Provedor, Callable[..., list[_ItemBruto]]]] = []
        if self._tavily_api_key:
            coletores.append(("tavily", self._tavily))
        coletores.append(("google_news_rss", self._rss))

        erros: list[str] = []
        provedor: Provedor = "nenhum"
        noticias: list[Noticia] = []
        descartadas = irrelevantes = 0
        for nome, coletar in coletores:
            try:
                itens = coletar(termo, inicio, fim, erros)
            # StreamError e InvalidURL não herdam de HTTPError; RecursionError vem do json.loads
            # de um corpo aninhado demais. Qualquer um deles tem de deixar o RSS ser tentado.
            except (
                httpx.HTTPError,
                httpx.StreamError,
                httpx.InvalidURL,
                ValueError,
                RecursionError,
            ) as erro:
                erros.append(self._descrever_erro(nome, erro))
                continue
            provedor = nome
            noticias, descartadas_aqui, irrelevantes_aqui = self._filtrar(
                itens, janela, inicio, fim, nome, erros
            )
            descartadas += descartadas_aqui
            irrelevantes += irrelevantes_aqui
            if noticias:
                break
            erros.append(
                f"{nome}: nenhuma notícia aproveitável após os filtros de janela, injeção e "
                "relevância"
            )

        noticias.sort(key=lambda n: not n.confiavel)
        return ResultadoBusca(
            termo=termo,
            janela=janela,
            inicio=inicio,
            fim=fim,
            provedor=provedor,
            noticias=noticias[: self._max],
            descartadas_por_injecao=descartadas,
            descartadas_por_irrelevancia=irrelevantes,
            erros=erros,
        )
