"""Arredondamento do percentual como se aprende na escola: meio para cima, em decimal exato.

O `format` do Python arredonda empates "para o par" (31,25 vira 31,2) e parte do binário, que
nem sempre é o decimal esperado. Redator e relatório mostram o mesmo valor: 31,25 é 31,3.
"""

from decimal import ROUND_HALF_UP, Decimal


def _meio_para_cima(valor: Decimal, casas: int) -> str:
    passo = Decimal(1).scaleb(-casas)
    return f"{valor.quantize(passo, rounding=ROUND_HALF_UP)}".replace(".", ",")


def pontos(valor: float, casas: int = 1) -> str:
    """Valor já em pontos percentuais: `31.25` -> `"31,3"` (sem o `%`)."""
    return _meio_para_cima(Decimal(repr(float(valor))), casas)


def percentual(fracao: float, casas: int = 1) -> str:
    """Fração da base 1: `0.3125` -> `"31,3"` (sem o `%`); parte da forma curta do float."""
    return _meio_para_cima(Decimal(repr(float(fracao))) * 100, casas)
