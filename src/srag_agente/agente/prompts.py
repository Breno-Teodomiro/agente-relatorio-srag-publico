"""Prompts versionados em arquivos; o hash do conjunto é gravado em cada execução."""

import hashlib
from pathlib import Path

PASTA_PROMPTS = Path(__file__).parent / "prompts"


def carregar_prompt(nome: str) -> str:
    return (PASTA_PROMPTS / f"{nome}.md").read_text(encoding="utf-8")


def hash_prompts() -> str:
    resumo = hashlib.sha256()
    for arquivo in sorted(PASTA_PROMPTS.glob("*.md")):
        resumo.update(arquivo.name.encode())
        resumo.update(arquivo.read_bytes())
    return resumo.hexdigest()[:16]
