"""Séries dos dois gráficos obrigatórios e distribuições de perfil dos casos.

Supressão entre a série diária e os agregados da mesma página: além das barras, o relatório
publica somas exatas de pedaços da janela de 30 dias (semana atual e anterior do aumento, dias em
consolidação, cada mês). Por isso a supressão complementar da série diária roda por bloco dessas
janelas (`janelas_publicadas` + `suprimir_com_janelas`), e o mês que cabe inteiro na série diária
não serve de complemento na série mensal.

Limites que sobram (registrados no ADR 0011):
1. Bloco sem nenhuma célula >= 5 (só zeros e "<5") não tem o que esconder. Se a soma do bloco
   fixar um "<5" (um "<5" entre zeros; dois "<5" somando 8), só mascarar o agregado da janela
   protege, e isso é no catálogo/relatório. Some quando a janela publicada é ela própria "<5".
2. O corte do mês corrente pode criar bloco de um dia só (dia de referência 1, 13, 15, 20, 22,
   27 ou 29; em 1º de março, depois de um fevereiro de 28 dias, são três blocos): o dia sai
   direto de numerador, excluídos e rótulo do mês, sem olhar a série diária. A
   base atual tem referência em 25/06/2025 (blocos de 2 a 14 dias); outra base pode cair aí.
3. A proteção supõe que as janelas acima são as únicas somas exatas que tocam os 30 dias. Métrica
   nova sobre dias (ou mudança das janelas em 03_gold.sql) precisa entrar em `janelas_publicadas`;
   o teste de integração confere que as janelas daqui somam o mesmo que `gold.janelas_aumento`.
4. O mês anterior entra só pela parte que está na série diária: os dias dele fora dos 30 dias não
   são publicados um a um, então a soma do mês não fecha a conta sozinha.
5. Arredondamento do SVG: a barra de célula escondida não é desenhada (altura 0, marca própria) e
   a escala do eixo vem só das visíveis; as coordenadas com 5 casas revelam só o que já é exato.
"""

from datetime import date, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel

from srag_agente.metricas.repositorio import DadosIndisponiveis, Referencia
from srag_agente.metricas.supressao import (
    LIMIAR_SUPRESSAO,
    suprimir,
    suprimir_celulas,
    suprimir_com_janelas,
)

TipoSerie = Literal["diaria_30d", "mensal_12m"]
Dimensao = Literal["classificacao", "faixa_etaria"]

NOTA_SUPRESSAO = (
    'Contagens de 1 a 4 aparecem como "<5". Quando há alguma, a menor contagem exibida também '
    'aparece como "oculto" (supressão complementar): sem isso, o total menos as demais '
    "revelaria o valor escondido. O total é exato e inclui as células escondidas."
)

NOTAS: dict[str, str] = {
    "diaria_30d": "Últimos 30 dias até a data de referência. Os 14 dias mais recentes ainda estão "
    "em consolidação (notificações atrasadas). " + NOTA_SUPRESSAO + " Aqui o oculto fica no mesmo "
    "trecho do valor escondido (cada semana do aumento de casos, os dias em consolidação, cada "
    "mês), porque a soma desses trechos também é publicada.",
    "mensal_12m": "Últimos 12 meses até a data de referência. Meses anteriores ao início da base "
    "aparecem sem dados; o primeiro e o último mês podem estar incompletos. "
    + NOTA_SUPRESSAO
    + " O mês que cabe inteiro na série diária nunca é o oculto: as barras diárias o revelariam.",
}

# Janela da série diária: generate_series(data_referencia - 29, data_referencia) no repositório.
DIAS_SERIE_DIARIA = 30


class FonteSeries(Protocol):
    def referencia(self) -> Referencia: ...
    def serie_diaria(self, uf: str) -> list[dict]: ...
    def serie_mensal(self, uf: str) -> list[dict]: ...
    def distribuicao(self, dimensao: str, uf: str) -> list[dict]: ...


class PontoSerie(BaseModel):
    rotulo: str
    casos: int | str | None
    em_consolidacao: bool = False
    fora_da_base: bool = False
    parcial: bool = False


class Serie(BaseModel):
    tipo: TipoSerie
    uf: str
    data_referencia: date
    pontos: list[PontoSerie]
    total: int | str
    nota: str


class CategoriaDistribuicao(BaseModel):
    categoria: str
    casos: int | str
    percentual: float | None


class Distribuicao(BaseModel):
    dimensao: Dimensao
    uf: str
    total: int | str
    categorias: list[CategoriaDistribuicao]
    nota: str = NOTA_SUPRESSAO


def _ponto(linha: dict, tipo: TipoSerie, casos: int | str | None) -> PontoSerie:
    """`casos` já vem suprimido: a supressão complementar depende da série inteira."""
    if tipo == "diaria_30d":
        return PontoSerie(
            rotulo=linha["dia"].isoformat(),
            casos=casos,
            em_consolidacao=linha["em_consolidacao"],
        )
    return PontoSerie(
        rotulo=linha["mes"],
        casos=casos,
        fora_da_base=linha["fora_da_base"],
        parcial=linha["parcial"],
    )


def janelas_publicadas(dias: list[date], data_consolidada: date) -> dict[str, set[int]]:
    """Posições da série diária cuja soma sai exata em outro lugar do relatório.

    Só entram janelas que cabem inteiras na série: as do aumento (repetem `gold.janelas_aumento`,
    03_gold.sql; o teste de integração confere que somam o mesmo) e o mês que cabe inteiro
    (rótulo e barra do gráfico mensal). Janela que passa da série (mês anterior, 30d do
    complemento) não entra: os dias dela fora da série não são publicados um a um, então a soma
    não isola a parte de dentro, e as combinações dessas janelas só cortam a série no início do
    mês e da consolidação, cortes que já estão aqui. Incluí-las criaria blocos de um dia que não
    dá para proteger, sem ganho.
    """

    def entre(inicio: date, fim: date) -> set[int]:
        return {i for i, dia in enumerate(dias) if inicio <= dia <= fim}

    c = data_consolidada
    janelas = {
        "aumento_7d_atual": entre(c - timedelta(days=6), c),
        "aumento_7d_anterior": entre(c - timedelta(days=13), c - timedelta(days=7)),
        "em_consolidacao": {i for i, dia in enumerate(dias) if dia > c},
    }
    # Mês que começa dentro da série cabe nela: ou termina antes, ou é o corrente, que termina na
    # data de referência (a maior data da base).
    for mes in sorted({f"{dia:%Y-%m}" for dia in dias}):
        if date.fromisoformat(f"{mes}-01") >= dias[0]:
            janelas[f"mes_{mes}"] = {i for i, dia in enumerate(dias) if f"{dia:%Y-%m}" == mes}
    return janelas


def _suprimir_mensal(linhas: list[dict], referencia: Referencia) -> list[int | str | None]:
    """Mês inteiro dentro da série diária, com 5 ou mais, fica de fora da supressão complementar.

    Se ele fosse o oculto, a soma das barras diárias o devolveria e total - exibidos revelaria o
    "<5". Fora do problema ele sai exato; quem lê sabe disso, então o problema dos demais meses é
    o mesmo de uma resposta com total = total - esse mês.
    """
    inicio_diaria = referencia.data_referencia - timedelta(days=DIAS_SERIE_DIARIA - 1)
    fixos = {
        i
        for i, linha in enumerate(linhas)
        if date.fromisoformat(f"{linha['mes']}-01") >= inicio_diaria
        and (linha["casos"] or 0) >= LIMIAR_SUPRESSAO
    }
    exibidos: list[int | str | None] = [linha["casos"] for linha in linhas]
    demais = [i for i in range(len(linhas)) if i not in fixos]
    for i, valor in zip(
        demais, suprimir_celulas([linhas[i]["casos"] for i in demais]), strict=True
    ):
        exibidos[i] = valor
    return exibidos


def calcular_serie(tipo: TipoSerie, fonte: FonteSeries, uf: str = "BR") -> Serie:
    referencia = fonte.referencia()
    linhas = fonte.serie_diaria(uf) if tipo == "diaria_30d" else fonte.serie_mensal(uf)
    total = sum(linha["casos"] or 0 for linha in linhas)
    if tipo == "diaria_30d":
        janelas = janelas_publicadas(
            [linha["dia"] for linha in linhas], referencia.data_consolidada
        )
        exibidos = suprimir_com_janelas([linha["casos"] for linha in linhas], janelas.values())
    else:
        exibidos = _suprimir_mensal(linhas, referencia)
    return Serie(
        tipo=tipo,
        uf=uf,
        data_referencia=referencia.data_referencia,
        pontos=[_ponto(linha, tipo, casos) for linha, casos in zip(linhas, exibidos, strict=True)],
        total=suprimir(total),
        nota=NOTAS[tipo],
    )


def calcular_distribuicao(dimensao: Dimensao, fonte: FonteSeries, uf: str = "BR") -> Distribuicao:
    linhas = fonte.distribuicao(dimensao, uf)
    if not linhas:
        raise DadosIndisponiveis(f"sem casos para a UF {uf}")
    total = sum(linha["casos"] for linha in linhas)
    exibidos = suprimir_celulas([linha["casos"] for linha in linhas])
    return Distribuicao(
        dimensao=dimensao,
        uf=uf,
        total=suprimir(total),
        categorias=[
            CategoriaDistribuicao(
                categoria=linha["categoria"],
                casos=casos,
                # Percentual de célula escondida revelaria a contagem (casos = percentual x total).
                percentual=linha["casos"] / total if isinstance(casos, int) else None,
            )
            for linha, casos in zip(linhas, exibidos, strict=True)
        ],
    )
