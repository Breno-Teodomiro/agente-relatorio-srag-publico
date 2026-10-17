"""Os dois gráficos obrigatórios: SVG (HTML, temas escuro e claro) e PNG (Markdown/README)."""

import io
import re
from typing import Literal

import matplotlib

matplotlib.use("Agg")  # antes do pyplot: sem janela, só arquivo
import matplotlib.pyplot as plt
from markupsafe import escape
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from srag_agente.metricas.supressao import ROTULO_OCULTO, ROTULO_SUPRIMIDO
from srag_agente.relatorio.formatacao import data_br, inteiro, mes_br

Tema = Literal["escuro", "claro"]
Formato = Literal["svg", "png"]

plt.rcParams["svg.fonttype"] = "none"  # texto continua texto no SVG (menor e acessível)
plt.rcParams["svg.hashsalt"] = "srag-agente"  # ids de clip/hatch estáveis: mesmo dado, mesmo SVG

TEMAS: dict[str, dict[str, str]] = {
    "escuro": {
        "fundo": "#24272e",
        "texto": "#d6d9de",
        "grade": "#3a3f48",
        "barra": "#6fa8dc",
        "clara": "#3d5a73",
        "nota": "#9aa0a8",
        "alerta": "#e8b07c",
    },
    "claro": {
        "fundo": "#ffffff",
        "texto": "#2b2f36",
        "grade": "#e3e6ea",
        "barra": "#2f6db5",
        "clara": "#a9c4e6",
        "nota": "#5f6670",
        "alerta": "#a8590f",
    },
}

# Célula escondida não é zero: não vira barra, ganha marca própria (losango vazio para "<5",
# "x" para "oculto") e entra na legenda só quando existe na série.
MARCAS = {
    ROTULO_SUPRIMIDO: ("D", "Menos de 5 casos (<5)"),
    ROTULO_OCULTO: ("X", "Oculto (supressão complementar)"),
}


def _altura(casos: int | str | None) -> float:
    """Altura da barra. Texto ("<5", "oculto") e None não têm altura: ficam sem barra, e quem
    desenha põe a marca; um zero aqui seria lido como "nenhum caso"."""
    return float(casos) if isinstance(casos, int) else 0.0


def _figura(tema: Tema):
    cores = TEMAS[tema]
    figura, eixo = plt.subplots(figsize=(9, 3.6), dpi=110)
    figura.patch.set_facecolor(cores["fundo"])
    eixo.set_facecolor(cores["fundo"])
    for lado in ("top", "right"):
        eixo.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        eixo.spines[lado].set_color(cores["grade"])
    eixo.tick_params(colors=cores["texto"], labelsize=8)
    eixo.yaxis.grid(True, color=cores["grade"], linewidth=0.6)
    eixo.set_axisbelow(True)
    return figura, eixo, cores


def _marcar_escondidas(eixo, pontos: list[dict], cores: dict[str, str]) -> list[Line2D]:
    """Desenha a marca de cada célula escondida e devolve as entradas de legenda usadas."""
    topo = max((_altura(p["casos"]) for p in pontos), default=0.0) or 1.0
    eixo.set_ylim(-0.06 * topo, topo * 1.15)
    entradas = []
    for rotulo, (marcador, legenda) in MARCAS.items():
        posicoes = [i for i, p in enumerate(pontos) if p["casos"] == rotulo]
        if not posicoes:
            continue
        eixo.plot(
            posicoes,
            [0] * len(posicoes),
            linestyle="none",
            marker=marcador,
            markersize=7,
            markerfacecolor="none" if rotulo == ROTULO_SUPRIMIDO else cores["alerta"],
            markeredgecolor=cores["alerta"],
            clip_on=False,
        )
        entradas.append(
            Line2D(
                [],
                [],
                linestyle="none",
                marker=marcador,
                markersize=7,
                markerfacecolor="none" if rotulo == ROTULO_SUPRIMIDO else cores["alerta"],
                markeredgecolor=cores["alerta"],
                label=legenda,
            )
        )
    return entradas


def _legenda(eixo, itens: list, cores: dict[str, str]) -> None:
    """Fora da área do gráfico: dentro dela a legenda cobriria as barras mais altas."""
    eixo.legend(
        handles=itens,
        frameon=False,
        labelcolor=cores["texto"],
        fontsize=8,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=2,
    )


def _prefixar_ids(svg: str, prefixo: str) -> str:
    """Os 4 SVGs vão na mesma página: ids repetidos (figure_1, patch_1…) quebram url(#id)."""
    ids = set(re.findall(r'\bid="([^"]+)"', svg))
    return re.sub(
        r'(\bid="|url\(#|href="#)([^"\)]+)',
        lambda m: m.group(1) + (f"{prefixo}-{m.group(2)}" if m.group(2) in ids else m.group(2)),
        svg,
    )


def _exportar(figura, formato: Formato, prefixo: str, descricao: str) -> bytes:
    titulo = figura.axes[0].get_title(loc="left")
    buffer = io.BytesIO()
    figura.savefig(
        buffer,
        format=formato,
        bbox_inches="tight",
        facecolor=figura.get_facecolor(),
        metadata={"Date": None} if formato == "svg" else None,  # sem carimbo de data no SVG
    )
    plt.close(figura)
    if formato == "png":
        return buffer.getvalue()
    svg = _prefixar_ids(buffer.getvalue().decode("utf-8"), prefixo)
    rotulo = f'role="img" aria-label="{escape(f"{titulo}. {descricao}")}"'
    return svg.replace("<svg ", f"<svg {rotulo} ", 1).encode("utf-8")


def grafico_diario(serie: dict, tema: Tema, formato: Formato = "svg") -> bytes:
    figura, eixo, cores = _figura(tema)
    pontos = serie["pontos"]
    barras = eixo.bar(
        range(len(pontos)),
        [_altura(p["casos"]) for p in pontos],
        color=[cores["clara"] if p["em_consolidacao"] else cores["barra"] for p in pontos],
    )
    for barra, ponto in zip(barras, pontos, strict=True):
        if ponto["em_consolidacao"]:
            barra.set_hatch("//")
            barra.set_edgecolor(cores["fundo"])
    marcas = _marcar_escondidas(eixo, pontos, cores)
    indices = list(range(0, len(pontos), 5))
    eixo.set_xticks(indices, [data_br(pontos[i]["rotulo"])[:5] for i in indices])
    eixo.set_title(
        f"Casos diários de SRAG — 30 dias até {data_br(serie['data_referencia'])}",
        color=cores["texto"],
        fontsize=10,
        loc="left",
    )
    _legenda(
        eixo,
        [
            Patch(color=cores["barra"], label="Consolidado"),
            Patch(
                facecolor=cores["clara"],
                hatch="//",
                label="Em consolidação (14 dias mais recentes)",
            ),
            *marcas,
        ],
        cores,
    )
    return _exportar(figura, formato, f"diario-{tema}", "Barras de casos por dia.")


def grafico_mensal(serie: dict, tema: Tema, formato: Formato = "svg") -> bytes:
    figura, eixo, cores = _figura(tema)
    pontos = serie["pontos"]
    posicoes = range(len(pontos))
    barras = eixo.bar(posicoes, [_altura(p["casos"]) for p in pontos], color=cores["barra"])
    for barra, ponto in zip(barras, pontos, strict=True):
        if ponto["parcial"]:
            barra.set_hatch("//")
            barra.set_edgecolor(cores["fundo"])
    marcas = _marcar_escondidas(eixo, pontos, cores)
    eixo.bar_label(
        barras,
        labels=[
            "" if p["fora_da_base"] or p["casos"] in MARCAS else inteiro(p["casos"]) for p in pontos
        ],
        fontsize=7,
        color=cores["texto"],
        padding=2,
    )
    topo = eixo.get_ylim()[1]
    for indice, ponto in enumerate(pontos):
        if ponto["fora_da_base"]:
            eixo.text(
                indice,
                0,
                "fora do\nperíodo\nda base",
                ha="center",
                va="bottom",
                fontsize=7,
                color=cores["nota"],
            )
        elif ponto["casos"] in MARCAS:
            eixo.text(
                indice,
                topo * 0.04,
                ponto["casos"],
                ha="center",
                va="bottom",
                fontsize=7,
                color=cores["alerta"],
            )
    eixo.set_xticks(list(posicoes), [mes_br(p["rotulo"]) for p in pontos])
    eixo.set_title(
        "Casos mensais de SRAG — últimos 12 meses", color=cores["texto"], fontsize=10, loc="left"
    )
    _legenda(
        eixo,
        [
            Patch(color=cores["barra"], label="Mês completo"),
            Patch(facecolor=cores["barra"], hatch="//", label="Mês parcial"),
            *marcas,
        ],
        cores,
    )
    return _exportar(figura, formato, f"mensal-{tema}", "Barras de casos por mês.")
