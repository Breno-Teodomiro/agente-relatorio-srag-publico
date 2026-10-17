"""Formas de ofuscação Unicode usadas nos casos de ataque (geradas, para não pôr o caractere
invisível ou de largura total direto no código-fonte)."""

LARGURA_ZERO = chr(0x200B)
JUNTOR = chr(0x2060)  # word joiner
INVERSAO = chr(0x202E)  # right-to-left override
FIM_INVERSAO = chr(0x202C)


def largura_total(texto: str, so_digitos: bool = False) -> str:
    """Troca cada caractere ASCII pela forma de largura total (U+FF01…), que o NFKC desfaz."""
    return "".join(
        chr(ord(c) + 0xFEE0) if "!" <= c <= "~" and (c.isdigit() or not so_digitos) else c
        for c in texto
    )
