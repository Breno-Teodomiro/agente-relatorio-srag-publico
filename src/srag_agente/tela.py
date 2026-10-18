"""Lógica da tela (app.py), sem Streamlit: chama o núcleo e prepara o que a tela mostra.

O relatório vem do mesmo `gerar_relatorio` da CLI (mesmas guardrails e auditoria); a tela só lê
os arquivos que ele gravou.
"""

import base64
import threading
from collections import OrderedDict
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass

from srag_agente import nucleo
from srag_agente.config import Config
from srag_agente.erros import classificar
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


@dataclass(frozen=True)
class FalhaTela:
    """A falha como a tela a mostra: só a mensagem já tratada (sem traceback nem dado cru)."""

    mensagem: str


class ArmazemResultados:
    """Último resultado por sessão, guardado no servidor (fora do `st.session_state`).

    O Streamlit interrompe a execução antiga quando chega um novo clique; escrever o resultado
    no `session_state` dali levanta a parada e o relatório se perde. Aqui a escrita não passa
    pelo Streamlit. Limitado: um resultado por sessão e no máximo `maximo` sessões (sai a menos
    lida ou gravada). Um erro entra como `FalhaTela`: a exceção, com traceback e variáveis
    locais, não fica viva aqui.
    """

    def __init__(self, maximo: int = 20) -> None:
        self._maximo = maximo
        self._itens: OrderedDict[str, object] = OrderedDict()
        self._lock = threading.Lock()

    def guardar(self, sessao: str, valor: object) -> None:
        if isinstance(valor, Exception):
            valor = FalhaTela(classificar(valor)[1])
        with self._lock:
            self._itens.pop(sessao, None)
            self._itens[sessao] = valor
            while len(self._itens) > self._maximo:
                self._itens.popitem(last=False)  # a sessão mais antiga sai

    def ler(self, sessao: str) -> object | None:
        with self._lock:
            if sessao in self._itens:
                self._itens.move_to_end(sessao)  # ler renova: quem está em uso não é descartado
            return self._itens.get(sessao)


def executar_controlado[T](
    trava: threading.Lock,
    estado: MutableMapping,
    teto: int,
    executar: Callable[[], T],
    guardar: Callable[[T | Exception], None] | None = None,
) -> T:
    """Roda `executar` sob a trava global e o teto da sessão; o controle é do servidor.

    Cliques repetidos ou abas paralelas não geram relatórios simultâneos nem passam do teto.
    A tentativa conta mesmo se falhar (o crédito pode ter sido gasto) e a trava sempre sai.
    `guardar` recebe o resultado (ou o erro) ainda aqui dentro, antes de qualquer escrita que o
    Streamlit possa interromper.
    """
    if teto_atingido(estado, teto):
        raise TetoDeRelatoriosError
    if not trava.acquire(blocking=False):
        raise RelatorioEmAndamentoError
    try:
        estado[CHAVE_USADOS] = estado.get(CHAVE_USADOS, 0) + 1
        try:
            resultado = executar()
        except Exception as erro:
            if guardar:
                guardar(erro)
            raise
        if guardar:
            guardar(resultado)
        return resultado
    finally:
        trava.release()


def executar_e_guardar[T](
    armazem: ArmazemResultados,
    sessao: str,
    trava: threading.Lock,
    estado: MutableMapping,
    teto: int,
    executar: Callable[[], T],
) -> None:
    """`executar_controlado` + guarda o resultado da sessão; trava e teto continuam sendo erro.

    Qualquer outra falha vira o resultado da sessão, inclusive a que acontece antes de
    `executar` (contador, spinner): a tela nunca mostra o relatório anterior como se fosse o atual.
    """
    guardou = False

    def guardar(valor: T | Exception) -> None:
        nonlocal guardou
        armazem.guardar(sessao, valor)
        guardou = True

    try:
        executar_controlado(trava, estado, teto, executar, guardar=guardar)
    except (RelatorioEmAndamentoError, TetoDeRelatoriosError):
        raise
    except Exception as erro:
        if not guardou:
            armazem.guardar(sessao, erro)
