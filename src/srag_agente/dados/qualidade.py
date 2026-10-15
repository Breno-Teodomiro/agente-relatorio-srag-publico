"""Gera docs/QUALIDADE_DADOS.md a partir de silver.qualidade (o número nunca é escrito à mão)."""

from datetime import date
from pathlib import Path

import psycopg


def gerar_relatorio_qualidade(conn: psycopg.Connection, destino: Path) -> Path:
    regras = conn.execute(
        "SELECT regra, coluna, linhas_afetadas, total_linhas FROM silver.qualidade "
        "ORDER BY regra, coluna"
    ).fetchall()
    total = regras[0][3] if regras else 0
    linhas = [
        "# Qualidade dos dados",
        "",
        f"> Gerado por `make qualidade` em {date.today():%d/%m/%Y}. Não editar à mão.",
        "",
        f"Linhas na raw: **{total:,}**".replace(",", "."),
        "",
        "| Regra | Coluna | Linhas afetadas | % do total |",
        "|---|---|---:|---:|",
    ]
    for regra, coluna, afetadas, _ in regras:
        afetadas_fmt = f"{afetadas:,}".replace(",", ".")
        percentual = f"{afetadas / total * 100:.3f}".replace(".", ",") if total else "0"
        linhas.append(f"| {regra} | `{coluna}` | {afetadas_fmt} | {percentual}% |")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return destino
