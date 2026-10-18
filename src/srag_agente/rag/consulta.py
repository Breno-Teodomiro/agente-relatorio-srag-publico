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
    # NUL e controle (escape de terminal, DEL) não têm uso numa pergunta; o NUL nem cabe num
    # texto do Postgres. Tab e nova linha continuam valendo.
    if any((ord(c) < 32 and c not in "\t\n\r") or 127 <= ord(c) < 160 for c in limpa):
        raise PerguntaInvalidaError(
            "A pergunta tem caractere de controle (invisível); escreva só o texto da pergunta."
        )
    return limpa


def origem(trecho: TrechoDicionario, metodo: str = "vetor") -> str:
    """Distância 0 marca o campo citado pelo nome (busca exata); o resto veio do embedding
    (`vetor`) ou da busca de texto completo (`texto`, sem modelo)."""
    if trecho.distancia == 0.0:
        return "nome exato"
    if metodo == "texto":
        return "texto, sem modelo"
    return f"similaridade {1 - trecho.distancia:.2f}"


def responder(busca: Busca, pergunta: str) -> list[Resposta]:
    metodo = getattr(busca, "metodo", "vetor")
    return [
        Resposta(t.campo, t.pagina, t.texto, origem(t, metodo))
        for t in busca.buscar(validar_pergunta(pergunta))
    ]


@contextmanager
def abrir_busca(cfg: Config, embedder: Embedder | None = None) -> Iterator[BuscaDicionario]:
    """Busca com o papel `agente` (só lê o gold). `embedder` já carregado evita recarregar o
    modelo a cada consulta (a tela o guarda em cache). Em `busca_dicionario == "texto"` o modelo
    nunca é carregado (o `embedder` recebido é ignorado)."""
    if cfg.busca_dicionario == "texto":
        embedder = None
    else:
        embedder = embedder or criar_embedder(cfg.pasta_cache_modelos)
    with conectar(cfg, "agente", autocommit=True) as conn:
        yield BuscaDicionario(conn, embedder, K_TRECHOS)
