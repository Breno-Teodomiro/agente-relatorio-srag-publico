"""Confere, por código, se o orquestrador juntou tudo o que o relatório exige."""

from srag_agente.ferramentas.registro import Evidencia

METRICAS_OBRIGATORIAS = ("aumento_casos", "mortalidade", "uti", "vacinacao")
SERIES_OBRIGATORIAS = ("diaria_30d", "mensal_12m")
JANELAS_OBRIGATORIAS = ("periodo_dos_dados", "cenario_atual")


def faltantes(evidencias: list[Evidencia], uf: str) -> list[str]:
    metricas = {
        e.dados["chave"]
        for e in evidencias
        if e.ferramenta == "consultar_metricas" and e.dados["uf"] == uf
    }
    series = {
        e.dados["tipo"]
        for e in evidencias
        if e.ferramenta == "consultar_serie" and e.dados["uf"] == uf
    }
    janelas = {e.dados["janela"] for e in evidencias if e.ferramenta == "buscar_noticias"}
    return (
        [f"metrica:{m}" for m in METRICAS_OBRIGATORIAS if m not in metricas]
        + [f"serie:{s}" for s in SERIES_OBRIGATORIAS if s not in series]
        + [f"noticias:{j}" for j in JANELAS_OBRIGATORIAS if j not in janelas]
    )
