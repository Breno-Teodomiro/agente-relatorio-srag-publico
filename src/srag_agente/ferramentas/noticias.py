"""Busca de notícias em tempo real: Tavily (principal) com Google News RSS de reserva."""

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
    limpar_texto,
    sanitizar_texto_externo,
)

Janela = Literal["periodo_dos_dados", "cenario_atual"]
URL_TAVILY = "https://api.tavily.com/search"
URL_GOOGLE_NEWS = "https://news.google.com/rss/search"
TIMEOUT_S = 20


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
    provedor: Literal["tavily", "google_news_rss", "nenhum"]
    noticias: list[Noticia]
    descartadas_por_injecao: int = 0
    erros: list[str] = []


@dataclass(frozen=True)
class _ItemBruto:
    titulo: str
    url: str
    url_fonte: str
    fonte: str
    data: date | None
    texto: str


def _data(valor: str | None) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(valor).date()
    except (TypeError, ValueError):
        return None


class BuscadorNoticias:
    def __init__(self, http: httpx.Client, tavily_api_key: str | None, max_resultados: int = 5):
        self._http = http
        self._tavily_api_key = tavily_api_key
        self._max = max_resultados

    def _tavily(self, termo: str, inicio: date, fim: date) -> list[_ItemBruto]:
        resposta = self._http.post(
            URL_TAVILY,
            json={
                "query": termo,
                "topic": "news",
                "start_date": inicio.isoformat(),
                "end_date": fim.isoformat(),
                "max_results": self._max * 2,
                "search_depth": "basic",
            },
            headers={"Authorization": f"Bearer {self._tavily_api_key}"},
            timeout=TIMEOUT_S,
        )
        resposta.raise_for_status()
        return [
            _ItemBruto(
                titulo=item.get("title", ""),
                url=item.get("url", ""),
                url_fonte=item.get("url", ""),
                fonte=urlparse(item.get("url", "")).hostname or "",
                data=_data(item.get("published_date")),
                texto=item.get("content", ""),
            )
            for item in resposta.json().get("results", [])
        ]

    def _rss(self, termo: str, inicio: date, fim: date) -> list[_ItemBruto]:
        consulta = (
            f"{termo} after:{inicio.isoformat()} before:{(fim + timedelta(days=1)).isoformat()}"
        )
        resposta = self._http.get(
            URL_GOOGLE_NEWS,
            params={"q": consulta, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"},
            timeout=TIMEOUT_S,
        )
        resposta.raise_for_status()
        itens = []
        for entrada in feedparser.parse(resposta.content).entries:
            fonte = entrada.get("source", {})
            publicado = entrada.get("published_parsed")
            itens.append(
                _ItemBruto(
                    titulo=entrada.get("title", ""),
                    url=entrada.get("link", ""),
                    url_fonte=fonte.get("href", entrada.get("link", "")),
                    fonte=fonte.get("title", ""),
                    data=date(*publicado[:3]) if publicado else None,
                    texto=entrada.get("summary", ""),
                )
            )
        return itens

    def _coletar(
        self, termo: str, inicio: date, fim: date
    ) -> tuple[str, list[_ItemBruto], list[str]]:
        erros: list[str] = []
        if self._tavily_api_key:
            try:
                itens = self._tavily(termo, inicio, fim)
                if itens:
                    return "tavily", itens, erros
            except httpx.HTTPError as erro:
                erros.append(f"tavily: {erro}")
        try:
            return "google_news_rss", self._rss(termo, inicio, fim), erros
        except httpx.HTTPError as erro:
            erros.append(f"google_news_rss: {erro}")
        return "nenhum", [], erros

    def buscar(self, termo: str, janela: Janela, inicio: date, fim: date) -> ResultadoBusca:
        provedor, itens, erros = self._coletar(termo, inicio, fim)
        noticias: list[Noticia] = []
        descartadas = 0
        for item in itens:
            if item.data is not None and not inicio <= item.data <= fim:
                continue
            titulo = sanitizar_texto_externo(item.titulo, limite=200)
            trecho = sanitizar_texto_externo(item.texto)
            if titulo is None or trecho is None:
                descartadas += 1
                continue
            noticias.append(
                Noticia(
                    titulo=titulo,
                    fonte=limpar_texto(item.fonte, limite=80),
                    url=item.url,
                    data=item.data,
                    trecho=trecho,
                    confiavel=dominio_confiavel(item.url_fonte),
                    janela=janela,
                )
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
            erros=erros,
        )
