"""Supressão de células pequenas: contagens de 1 a 4 não são exibidas (risco de reidentificação)."""

LIMIAR_SUPRESSAO = 5
ROTULO_SUPRIMIDO = "<5"


def suprimir(contagem: int) -> int | str:
    return ROTULO_SUPRIMIDO if 0 < contagem < LIMIAR_SUPRESSAO else contagem


def precisa_suprimir(*contagens: int) -> bool:
    return any(0 < c < LIMIAR_SUPRESSAO for c in contagens)
