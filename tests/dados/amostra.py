"""Amostra sintética no formato do CSV do DATASUS, gerada de forma determinística.

Os valores esperados de cada métrica são calculados a partir destas linhas por `oraculo.py`,
uma implementação em Python puro das mesmas definições do spec.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

REFERENCIA = date(2025, 6, 25)
INICIO = date(2025, 1, 1)

COLUNAS = [
    "NU_NOTIFIC",
    "DT_NOTIFIC",
    "DT_SIN_PRI",
    "SG_UF_NOT",
    "ID_MUNICIP",
    "CS_SEXO",
    "DT_NASC",
    "NU_IDADE_N",
    "TP_IDADE",
    "CS_RACA",
    "HOSPITAL",
    "DT_INTERNA",
    "UTI",
    "DT_ENTUTI",
    "SUPORT_VEN",
    "EVOLUCAO",
    "DT_EVOLUCA",
    "CLASSI_FIN",
    "VACINA",
    "VACINA_COV",
]

UFS = ["SP", "SP", "RJ", "MG", "PI"]


def casos_no_dia(dia: date) -> int:
    if date(2025, 5, 29) <= dia <= date(2025, 6, 4):
        return 10
    if date(2025, 6, 5) <= dia <= date(2025, 6, 11):
        return 12
    return 3 if dia.day % 2 else 4


def _linha(rnd: random.Random, numero: int, dia: date) -> dict[str, str]:
    hospital = rnd.choice(["1"] * 9 + ["2"])
    tipo_idade, idade = rnd.choice([("3", str(rnd.randint(1, 95))), ("2", str(rnd.randint(1, 11)))])
    notificacao = min(dia + timedelta(days=rnd.randint(0, 5)), REFERENCIA + timedelta(days=1))
    return {
        "NU_NOTIFIC": f"{900000 + numero}",
        "DT_NOTIFIC": notificacao.isoformat(),
        "DT_SIN_PRI": dia.isoformat(),
        "SG_UF_NOT": rnd.choice(UFS),
        "ID_MUNICIP": "MUNICIPIO SECRETO",
        "CS_SEXO": rnd.choice(["M", "F"]),
        "DT_NASC": "1961-09-19",
        "NU_IDADE_N": idade,
        "TP_IDADE": tipo_idade,
        "CS_RACA": "4",
        "HOSPITAL": hospital,
        "DT_INTERNA": (dia + timedelta(days=1)).isoformat() if hospital == "1" else "",
        "UTI": rnd.choice(["1", "2", "2", "9", ""]),
        "DT_ENTUTI": "",
        "SUPORT_VEN": rnd.choice(["1", "2", "3", "9", ""]),
        "EVOLUCAO": rnd.choice(["1"] * 6 + ["2", "3", "9", "", ""]),
        "DT_EVOLUCA": "",
        "CLASSI_FIN": rnd.choice(["1", "2", "4", "5", ""]),
        "VACINA": rnd.choice(["1", "2", "9", ""]),
        "VACINA_COV": rnd.choice(["1", "1", "2", "9", ""]),
    }


def _especiais() -> list[dict[str, str]]:
    """Linhas com os defeitos reais encontrados na base."""
    base = {c: "" for c in COLUNAS}
    base.update(
        {
            "DT_NOTIFIC": "2025-03-12",
            "DT_SIN_PRI": "2025-03-10",
            "SG_UF_NOT": "SP",
            "NU_IDADE_N": "40",
            "TP_IDADE": "3",
            "HOSPITAL": "1",
        }
    )
    return [
        {**base, "NU_NOTIFIC": "1", "DT_INTERNA": "1695-01-17 02:32:37.742690304"},
        {**base, "NU_NOTIFIC": "2", "DT_INTERNA": "2025-05-14 00:00:00.000000000"},
        {**base, "NU_NOTIFIC": "3", "NU_IDADE_N": "150"},
        {**base, "NU_NOTIFIC": "4", "SG_UF_NOT": "XX"},
        {**base, "NU_NOTIFIC": "5", "EVOLUCAO": "7"},
    ]


def gerar_linhas() -> list[dict[str, str]]:
    rnd = random.Random(42)
    linhas: list[dict[str, str]] = []
    dia = INICIO
    while dia <= REFERENCIA:
        for _ in range(casos_no_dia(dia)):
            linhas.append(_linha(rnd, len(linhas) + 1, dia))
        dia += timedelta(days=1)
    return linhas + _especiais()


def escrever_csv(caminho: Path, linhas: list[dict[str, str]]) -> Path:
    with caminho.open("w", encoding="latin-1", newline="") as arquivo:
        escritor = csv.DictWriter(
            arquivo, fieldnames=COLUNAS, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\n"
        )
        escritor.writeheader()
        escritor.writerows(linhas)
    return caminho
