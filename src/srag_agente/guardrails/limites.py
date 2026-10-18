"""Limites de execução: evitam laço infinito, conta alta e execução sem fim."""

import math
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Limites:
    max_chamadas_ferramenta: int = 12
    max_tokens: int = 60_000
    max_voltas_cobertura: int = 2
    # 1ª redação + 2 novas tentativas, cada uma com a instrução de correção (ADR 0005)
    max_tentativas_redacao: int = 3
    max_segundos: float = 300.0
    # Teto próprio de `buscar_noticias` (spec §3.3: duas janelas; a folga cobre um segundo termo).
    # A primeira execução real pediu 8 buscas e estourou o teto geral de chamadas.
    max_buscas_noticias: int = 4

    def __post_init__(self) -> None:
        for campo in fields(self):
            valor = getattr(self, campo.name)
            if isinstance(valor, bool) or not isinstance(valor, int | float):
                raise ValueError(f"{campo.name} precisa ser numérico, veio {valor!r}")
            if not math.isfinite(valor):  # nan escapa de "< 0" e "== 0"; inf é "sem teto"
                raise ValueError(f"{campo.name} precisa ser finito, veio {valor!r}")
            pode_ser_zero = campo.name == "max_voltas_cobertura"  # zero = não insiste
            if valor < 0 or (valor == 0 and not pode_ser_zero):
                raise ValueError(f"{campo.name} inválido: {valor!r}")


def _conferir(chamadas: int, tokens: int, segundos: float) -> None:
    if chamadas < 0 or tokens < 0 or segundos < 0:
        raise ValueError("contagem de execução negativa: erro de programação")


def motivo_de_parada(
    limites: Limites, chamadas: int, tokens: int, segundos: float = 0.0
) -> str | None:
    """Motivo para parar antes da próxima chamada, ou None. Atingir o teto já para."""
    _conferir(chamadas, tokens, segundos)
    if chamadas >= limites.max_chamadas_ferramenta:
        return f"limite de {limites.max_chamadas_ferramenta} chamadas de ferramenta atingido"
    if tokens >= limites.max_tokens:
        return f"limite de {limites.max_tokens} tokens atingido"
    if segundos >= limites.max_segundos:
        return f"limite de {limites.max_segundos:g} segundos de execução atingido"
    return None


# Ferramenta com teto próprio → campo de `Limites` que o guarda.
TETOS_POR_FERRAMENTA = {"buscar_noticias": "max_buscas_noticias"}


def motivo_de_recusa(limites: Limites, ferramenta: str | None, feitas: int) -> str | None:
    """Motivo para recusar mais uma chamada de `ferramenta`, que já teve `feitas`, ou None."""
    campo = TETOS_POR_FERRAMENTA.get(ferramenta or "")
    if campo is None:
        return None
    teto = getattr(limites, campo)
    if feitas < teto:
        return None
    return (
        f"limite de {teto} chamadas de {ferramenta} atingido; uma busca por janela basta: use as "
        "notícias já recebidas e siga com as outras ferramentas"
    )
