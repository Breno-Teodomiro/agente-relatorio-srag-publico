"""Filtros do template Markdown: todo texto de fora entra como texto, nunca como sintaxe."""

import re
from urllib.parse import quote

# [n] é citação; vira texto escapado só quando colada em "(", "[" ou ":" (viraria link/definição).
_ESPECIAIS = re.compile(r"\[\d+\](?![(\[:])|([\\`\[\]])")
_INICIO_DE_BLOCO = re.compile(r"^(?:[#>+*|=-]|\d+[.)])")
# GFM transforma "https://…" e "www.…" em link mesmo sem sintaxe: o escape em ":" e "." impede
_AUTOLINK = re.compile(r"(?<=[a-z0-9])(:)(?=//)|(?<![\w.-])(www)\.", re.IGNORECASE)
_URL_SEGUROS = "!#$&'*+,/:;=?@[]~-._%"  # o resto (inclusive < > ( ) espaço) é codificado


def md_texto(texto: str | None) -> str:
    """Texto livre (LLM, aviso, violação, pergunta) em uma linha, sem tag, link, imagem ou bloco.

    Escapa & < >, barra colchete e crase (sem ``![``, ``](`` nem definição de link), quebra o
    autolink (``https\\://``, ``www\\.``), junta as quebras de linha e protege o início (``#``,
    ``>``, ``-``, ``1.``) para não virar título, citação ou lista.
    """
    limpo = " ".join((texto or "").split())
    limpo = limpo.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    limpo = _ESPECIAIS.sub(lambda m: m.group(0) if m.group(1) is None else "\\" + m.group(1), limpo)
    limpo = _AUTOLINK.sub(lambda m: "\\:" if m.group(1) else m.group(2) + "\\.", limpo)
    if _INICIO_DE_BLOCO.match(limpo):
        limpo = "\\" + limpo
    return limpo


def md_url(url: str | None) -> str:
    """Destino de link no formato ``<url>``, com ``< > ( ) \\``, aspas, espaço e não-ASCII em %."""
    return f"<{quote(url or '', safe=_URL_SEGUROS)}>"
