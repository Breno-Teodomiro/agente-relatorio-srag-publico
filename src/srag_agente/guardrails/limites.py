"""Limites de execução: evitam laço infinito, conta alta e execução sem fim."""

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Limites:
    max_chamadas_ferramenta: int = 12
    max_tokens: int = 60_000
    max_voltas_cobertura: int = 2
    max_tentativas_redacao: int = 2  # 1ª redação + 1 nova tentativa (spec §6)
    max_segundos: float = 300.0

    def __post_init__(self) -> None:
        for campo in fields(self):
            valor = getattr(self, campo.name)
            if isinstance(valor, bool) or not isinstance(valor, int | float):
                raise ValueError(f"{campo.name} precisa ser numérico, veio {valor!r}")
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


def chamadas_restantes(limites: Limites, chamadas: int) -> int:
    """Quantas ferramentas de um lote paralelo ainda cabem (o resto não executa)."""
    _conferir(chamadas, 0, 0)
    return max(0, limites.max_chamadas_ferramenta - chamadas)
