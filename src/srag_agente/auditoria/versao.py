"""Versão do código registrada em cada execução (reprodutibilidade)."""

import os
import subprocess
from pathlib import Path

# O Git roda na pasta do pacote, não na pasta de onde a CLI foi chamada.
_PASTA_DO_CODIGO = Path(__file__).resolve().parent


def _git(*argumentos: str) -> str:
    return subprocess.run(
        ["git", *argumentos],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
        cwd=_PASTA_DO_CODIGO,
    ).stdout.strip()


def versao_codigo() -> str:
    """Commit atual (+ '-modificado' se houver alteração não commitada).

    Dentro do Docker não há Git: a versão vem da variável SRAG_VERSAO_CODIGO.
    Se o `git status` falhar (disco lento), o commit fica, com '-estado-desconhecido'.
    """
    if versao := os.environ.get("SRAG_VERSAO_CODIGO"):
        return versao
    try:
        commit = _git("rev-parse", "--short", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return "desconhecida"
    try:
        modificado = bool(_git("status", "--porcelain"))
    except (OSError, subprocess.SubprocessError):
        return f"{commit}-estado-desconhecido"
    return f"{commit}-modificado" if modificado else commit
