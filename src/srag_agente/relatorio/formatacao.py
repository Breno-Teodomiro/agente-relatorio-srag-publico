"""Formatação brasileira (vírgula decimal, ponto de milhar, dd/mm/aaaa)."""

from datetime import date

MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def pct(fracao: float | None, casas: int = 1) -> str:
    """Fração nula (métrica suprimida ou sem denominador) vira "—", nunca "0,0%"."""
    if fracao is None:
        return "—"
    return f"{fracao * 100:.{casas}f}".replace(".", ",") + "%"


def variacao(fracao: float | None) -> str:
    if fracao is None:
        return "—"
    sinal = "+" if fracao > 0 else "−" if fracao < 0 else ""  # noqa: RUF001 (menos tipográfico)
    return sinal + pct(abs(fracao))


def usd(valor: float, casas: int = 4) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def inteiro(valor: int | str | None) -> str:
    if valor is None:
        return "—"
    if isinstance(valor, str):
        # Texto é rótulo de supressão ("<5" ou "oculto") já decidido na camada de métricas:
        # passa como veio, sem converter nem recalcular.
        return valor
    return f"{valor:,}".replace(",", ".")


def data_br(valor: date | str | None) -> str:
    if not valor:
        return "—"
    data = date.fromisoformat(valor) if isinstance(valor, str) else valor
    return data.strftime("%d/%m/%Y")


def mes_br(rotulo: str) -> str:
    ano, mes = rotulo.split("-")
    return f"{MESES[int(mes) - 1]}/{ano[2:]}"
