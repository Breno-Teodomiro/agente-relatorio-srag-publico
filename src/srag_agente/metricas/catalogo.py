"""Catálogo declarativo das métricas: definição e limitação ficam junto do cálculo.

Métrica nova = uma view na gold + uma entrada em CATALOGO + um ramo em `_partes`.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from srag_agente.metricas.repositorio import Referencia
from srag_agente.metricas.supressao import precisa_suprimir, suprimir

ChaveMetrica = Literal["aumento_casos", "mortalidade", "uti", "vacinacao"]


@dataclass(frozen=True)
class DefinicaoMetrica:
    chave: ChaveMetrica
    nome: str
    definicao: str
    limitacao: str


CATALOGO: dict[str, DefinicaoMetrica] = {
    "aumento_casos": DefinicaoMetrica(
        "aumento_casos",
        "Taxa de aumento de casos",
        "Variação entre os casos dos 7 dias consolidados mais recentes e os dos 7 dias anteriores, "
        "pela data dos primeiros sintomas. Consolidado = até 14 dias antes da data de referência.",
        "Os 14 dias mais recentes ficam de fora porque ainda recebem notificações atrasadas; "
        "incluí-los mostraria uma queda que não aconteceu.",
    ),
    "mortalidade": DefinicaoMetrica(
        "mortalidade",
        "Taxa de mortalidade",
        "Óbitos por SRAG divididos pelos casos com desfecho conhecido (cura, óbito por SRAG ou "
        "óbito por outras causas), em todo o período da base.",
        "Casos ainda sem desfecho ou com desfecho ignorado ficam fora do denominador.",
    ),
    "uti": DefinicaoMetrica(
        "uti",
        "Taxa de internação em UTI",
        "Casos internados em UTI divididos pelos casos com essa informação preenchida "
        "(sim ou não).",
        "Não é a taxa de ocupação de leitos: a base não traz a capacidade de UTI. A ocupação real "
        "exigiria cruzar com o cadastro de leitos do CNES.",
    ),
    "vacinacao": DefinicaoMetrica(
        "vacinacao",
        "Taxa de vacinação contra COVID-19 entre os casos",
        "Casos com vacina contra COVID-19 divididos pelos casos com essa informação preenchida. "
        "A vacinação contra a gripe aparece como complemento.",
        "Mede a vacinação entre os internados por SRAG, não a cobertura vacinal da população.",
    ),
}

_RAZOES: dict[str, tuple[str, str]] = {
    "mortalidade": ("obitos", "com_desfecho"),
    "uti": ("uti_sim", "uti_informada"),
    "vacinacao": ("vacina_covid_sim", "vacina_covid_informada"),
}


class FonteIndicadores(Protocol):
    def referencia(self) -> Referencia: ...
    def indicadores(self, uf: str) -> dict: ...
    def janelas_aumento(self, uf: str) -> dict: ...


class ResultadoMetrica(BaseModel):
    chave: ChaveMetrica
    nome: str
    uf: str
    valor: float | None = Field(description="Fração: 0.0796 = 7,96%. Nulo se suprimido.")
    numerador: int | str
    denominador: int | str
    excluidos: int | str
    periodo_inicio: date
    periodo_fim: date
    definicao: str
    limitacao: str
    suprimido: bool = False
    complemento: dict[str, float | int | str | None] = Field(default_factory=dict)


def _taxa(numerador: int, denominador: int) -> float | None:
    return numerador / denominador if denominador else None


def _variacao(atual: int, anterior: int) -> float | None:
    return atual / anterior - 1 if anterior else None


def _suprimir_complemento(complemento: dict) -> dict:
    """Se alguma contagem do complemento for pequena, mascara as contagens e anula as taxas."""
    contagens = [v for v in complemento.values() if isinstance(v, int)]
    if not precisa_suprimir(*contagens):
        return complemento
    return {chave: suprimir(v) if isinstance(v, int) else None for chave, v in complemento.items()}


def _partes(chave: ChaveMetrica, fonte: FonteIndicadores, uf: str) -> dict:
    if chave == "aumento_casos":
        j = fonte.janelas_aumento(uf)
        consolidada = j["data_consolidada"]
        return {
            "numerador": j["casos_7d_atual"],
            "denominador": j["casos_7d_anterior"],
            "valor": _variacao(j["casos_7d_atual"], j["casos_7d_anterior"]),
            "excluidos": j["casos_em_consolidacao"],
            "periodo_inicio": consolidada - timedelta(days=13),
            "periodo_fim": consolidada,
            "complemento": _suprimir_complemento(
                {
                    "casos_30d_atual": j["casos_30d_atual"],
                    "casos_30d_anterior": j["casos_30d_anterior"],
                    "variacao_30d": _variacao(j["casos_30d_atual"], j["casos_30d_anterior"]),
                }
            ),
        }
    indicadores = fonte.indicadores(uf)
    campo_numerador, campo_denominador = _RAZOES[chave]
    numerador = indicadores[campo_numerador]
    denominador = indicadores[campo_denominador]
    complemento = {}
    if chave == "vacinacao":
        complemento = _suprimir_complemento(
            {
                "vacina_gripe": _taxa(
                    indicadores["vacina_gripe_sim"], indicadores["vacina_gripe_informada"]
                ),
                "vacina_gripe_numerador": indicadores["vacina_gripe_sim"],
                "vacina_gripe_denominador": indicadores["vacina_gripe_informada"],
            }
        )
    return {
        "numerador": numerador,
        "denominador": denominador,
        "valor": _taxa(numerador, denominador),
        "excluidos": indicadores["total_casos"] - denominador,
        "periodo_inicio": indicadores["periodo_inicio"],
        "periodo_fim": indicadores["periodo_fim"],
        "complemento": complemento,
    }


def calcular_metrica(
    chave: ChaveMetrica, fonte: FonteIndicadores, uf: str = "BR"
) -> ResultadoMetrica:
    definicao = CATALOGO[chave]
    partes = _partes(chave, fonte, uf)
    suprimido = precisa_suprimir(partes["numerador"], partes["denominador"])
    if suprimido:
        partes |= {
            "valor": None,
            "numerador": suprimir(partes["numerador"]),
            "denominador": suprimir(partes["denominador"]),
            "excluidos": suprimir(partes["excluidos"]),
            "complemento": {},
        }
    return ResultadoMetrica(
        chave=chave,
        nome=definicao.nome,
        uf=uf,
        definicao=definicao.definicao,
        limitacao=definicao.limitacao,
        suprimido=suprimido,
        **partes,
    )
