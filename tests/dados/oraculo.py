"""Implementação de referência, em Python puro, das regras de limpeza e das métricas.

Os testes comparam o SQL com este oráculo sobre a mesma amostra.
"""

import re
from collections import Counter
from datetime import date, timedelta

UFS_VALIDAS = {
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
}  # fmt: skip

_PREFIXO_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_IDADE = re.compile(r"[0-9]{1,3}")
_DIAS_POR_UNIDADE = {"1": 365, "2": 12, "3": 1}  # TP_IDADE: 1 = dias, 2 = meses, 3 = anos


def para_data(valor: str | None, minimo: date | None, maximo: date | None) -> date | None:
    if valor is None or minimo is None or maximo is None or not _PREFIXO_DATA.match(valor):
        return None
    try:
        d = date.fromisoformat(valor[:10])
    except ValueError:
        return None
    return d if minimo <= d <= maximo else None


def limites(linhas: list[dict]) -> tuple[date, date]:
    notificacoes = [date.fromisoformat(linha["DT_NOTIFIC"]) for linha in linhas]
    return min(notificacoes) - timedelta(days=365), max(notificacoes)


def _sim_nao(valor: str) -> str:
    return {"1": "sim", "2": "nao"}.get(valor, "ignorado")


def _evolucao(valor: str) -> str:
    if not valor.strip():
        return "sem_desfecho"
    return {"1": "cura", "2": "obito", "3": "obito_outras_causas"}.get(valor, "ignorado")


def _idade_anos(n: str | None, tp: str | None) -> int | None:
    """Idade em anos completos; `None` se o valor não é número ou passa de 120 anos."""
    if n is None or not _IDADE.fullmatch(n) or tp not in _DIAS_POR_UNIDADE:
        return None
    anos = int(n) // _DIAS_POR_UNIDADE[tp]
    return anos if 0 <= anos <= 120 else None


def faixa(n: str | None, tp: str | None) -> str:
    a = _idade_anos(n, tp)
    if a is None:
        return "ignorado"
    for teto, rotulo in ((0, "<1"), (4, "1-4"), (11, "5-11"), (17, "12-17"), (39, "18-39"),
                         (59, "40-59")):  # fmt: skip
        if a <= teto:
            return rotulo
    return "60+"


def _classificacao(valor: str) -> str:
    if not valor.strip():
        return "em_investigacao"
    return {
        "1": "influenza", "2": "outro_virus", "3": "outro_agente",
        "4": "nao_especificado", "5": "covid19",
    }.get(valor, "ignorado")  # fmt: skip


def limpar(linhas: list[dict]) -> list[dict]:
    minimo, maximo = limites(linhas)
    return [
        {
            "uf": linha["SG_UF_NOT"] if linha["SG_UF_NOT"] in UFS_VALIDAS else None,
            "faixa_etaria": faixa(linha["NU_IDADE_N"], linha["TP_IDADE"]),
            "dt_sintomas": para_data(linha["DT_SIN_PRI"], minimo, maximo),
            "dt_internacao": para_data(linha["DT_INTERNA"], minimo, maximo),
            "uti": _sim_nao(linha["UTI"]),
            "evolucao": _evolucao(linha["EVOLUCAO"]),
            "vacina_covid": _sim_nao(linha["VACINA_COV"]),
            "vacina_gripe": _sim_nao(linha["VACINA"]),
            "classificacao": _classificacao(linha["CLASSI_FIN"]),
        }
        for linha in linhas
    ]


def contar(casos: list[dict], campo: str) -> Counter:
    return Counter(c[campo] for c in casos)
