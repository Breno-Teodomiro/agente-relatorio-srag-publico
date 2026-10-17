"""Padrões de tentativa de injeção de instruções (prompt injection), em português e inglês."""

import re

_INJECAO = re.compile(
    r"(ignore|desconsidere|esque[çc]a)\s+(todas\s+as\s+|as\s+|suas\s+)?"
    r"(instru[çc][õo]es|regras|ordens)"
    r"|ignore\s+(all\s+)?(the\s+)?previous"
    r"|system\s+prompt|prompt\s+do\s+sistema"
    r"|voc[êe]\s+agora\s+[ée]|you\s+are\s+now"
    r"|jailbreak|\bDAN\b"
    r"|<\s*/?\s*(system|assistant|noticia_externa)\b",
    re.IGNORECASE,
)


def contem_injecao(texto: str) -> bool:
    return bool(_INJECAO.search(texto))
