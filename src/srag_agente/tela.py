"""Lógica da tela (app.py), sem Streamlit: chama o núcleo e prepara o que a tela mostra.

O relatório vem do mesmo `gerar_relatorio` da CLI (mesmas guardrails e auditoria); a tela só lê
os arquivos que ele gravou.
"""

import base64
import threading
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass

from srag_agente import nucleo
from srag_agente.config import Config
from srag_agente.relatorio.montar import ROTULOS_STATUS

CHAVE_USADOS = "relatorios_usados"  # contador por sessão, em st.session_state

_NIVEIS = {"ok": "sucesso", "parcial": "aviso", "revisao_humana": "aviso", "erro": "erro"}


def url_isolada(html: str) -> str:
    """O HTML como URL `data:`: o iframe ganha origem própria e não alcança a página da tela."""
    return "data:text/html;base64," + base64.b64encode(html.encode("utf-8")).decode("ascii")


@dataclass(frozen=True)
class RelatorioTela:
    situacao: str
    nivel: str  # "sucesso" | "aviso" | "erro"
    html: str  # autocontido: métricas, gráficos, notícias e trilha
    trilha: str  # auditoria.jsonl, para baixar
    custo_usd: float


def gerar_para_tela(
    cfg: Config,
    uf: str,
    pergunta: str | None,
    *,
    gerar: Callable[..., nucleo.ResultadoExecucao] = nucleo.gerar_relatorio,
    componentes: nucleo.Componentes | None = None,
) -> RelatorioTela:
    resultado = gerar(
        cfg, uf=uf, pergunta_extra=(pergunta or "").strip() or None, componentes=componentes
    )
    arquivos = resultado.arquivos
    return RelatorioTela(
        situacao=ROTULOS_STATUS.get(resultado.status, resultado.status),
        nivel=_NIVEIS.get(resultado.status, "aviso"),
        html=arquivos.html.read_text(encoding="utf-8"),
        trilha=arquivos.auditoria.read_text(encoding="utf-8"),
        custo_usd=resultado.custo_usd,
    )


class RelatorioEmAndamentoError(Exception):
    """Já há um relatório sendo gerado (a trava é global: vale para todas as sessões)."""


class TetoDeRelatoriosError(Exception):
    """A sessão já gastou o teto de relatórios."""


def teto_atingido(estado: MutableMapping, teto: int) -> bool:
    return estado.get(CHAVE_USADOS, 0) >= teto


def executar_controlado[T](
    trava: threading.Lock, estado: MutableMapping, teto: int, executar: Callable[[], T]
) -> T:
    """Roda `executar` sob a trava global e o teto da sessão; o controle é do servidor.

    Cliques repetidos ou abas paralelas não geram relatórios simultâneos nem passam do teto.
    A tentativa conta mesmo se falhar (o crédito pode ter sido gasto) e a trava sempre sai.
    """
    if teto_atingido(estado, teto):
        raise TetoDeRelatoriosError
    if not trava.acquire(blocking=False):
        raise RelatorioEmAndamentoError
    try:
        estado[CHAVE_USADOS] = estado.get(CHAVE_USADOS, 0) + 1
        return executar()
    finally:
        trava.release()
