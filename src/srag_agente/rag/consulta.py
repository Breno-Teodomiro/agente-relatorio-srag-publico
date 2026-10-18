"""Consulta direta ao dicionário de dados, sem LLM (CLI `srag dicionario` e aba da tela)."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.ferramentas.dicionario import BuscaDicionario, TrechoDicionario
from srag_agente.guardrails.entrada import LIMITE_PERGUNTA
from srag_agente.rag.embeddings import Embedder, criar_embedder

K_TRECHOS = 3


class PerguntaInvalidaError(ValueError):
    """Pergunta vazia ou maior que o limite do guardrail de entrada."""


class Busca(Protocol):
    def buscar(self, pergunta: str) -> list[TrechoDicionario]: ...


@dataclass(frozen=True)
class Resposta:
    campo: str | None
    pagina: int
    texto: str
    origem: str


def validar_pergunta(pergunta: str) -> str:
    limpa = pergunta.strip()
    if not limpa:
        raise PerguntaInvalidaError("Escreva uma pergunta sobre um campo do dicionário.")
    if len(limpa) > LIMITE_PERGUNTA:
        raise PerguntaInvalidaError(f"A pergunta tem mais de {LIMITE_PERGUNTA} caracteres.")
    return limpa


def origem(trecho: TrechoDicionario) -> str:
    """Distância 0 marca o campo citado pelo nome (busca exata); o resto veio do embedding."""
    if trecho.distancia == 0.0:
        return "nome exato"
    return f"similaridade {1 - trecho.distancia:.2f}"


def responder(busca: Busca, pergunta: str) -> list[Resposta]:
    return [
        Resposta(t.campo, t.pagina, t.texto, origem(t))
        for t in busca.buscar(validar_pergunta(pergunta))
    ]


@contextmanager
def abrir_busca(cfg: Config, embedder: Embedder | None = None) -> Iterator[BuscaDicionario]:
    """Busca com o papel `agente` (só lê o gold). `embedder` já carregado evita recarregar o
    modelo a cada consulta (a tela o guarda em cache)."""
    with conectar(cfg, "agente", autocommit=True) as conn:
        yield BuscaDicionario(conn, embedder or criar_embedder(cfg.pasta_cache_modelos), K_TRECHOS)
