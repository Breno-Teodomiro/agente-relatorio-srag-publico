"""Lógica da tela (app.py), sem Streamlit: chama o núcleo e prepara o que a tela mostra.

O relatório vem do mesmo `gerar_relatorio` da CLI (mesmas guardrails e auditoria); a tela só lê
os arquivos que ele gravou.

Demonstração online (`demo_online`, opt-in): a tela só abre se o ambiente passar em todas as
conferências daqui (banco remoto com TLS, só as credenciais da tela, opções do Streamlit que
protegem a página) e confere o teto diário dentro da trava global antes de gerar.
"""

import base64
import re
import tempfile
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from srag_agente import nucleo
from srag_agente.config import Config
from srag_agente.demo import FUSO_DEMO, SSLMODES_SEGUROS, host_local
from srag_agente.demo.teto import PacoteDemo, UsoDiario
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


def hoje_demo(agora: datetime | None = None) -> date:
    """Data de hoje no fuso da demonstração (Brasília), qualquer que seja o fuso da máquina."""
    return (agora or datetime.now(UTC)).astimezone(ZoneInfo(FUSO_DEMO)).date()


def gerar_para_tela(
    cfg: Config,
    uf: str,
    pergunta: str | None,
    *,
    gerar: Callable[..., nucleo.ResultadoExecucao] = nucleo.gerar_relatorio,
    componentes: nucleo.Componentes | None = None,
) -> RelatorioTela:
    if cfg.demo_online:
        # Pergunta complementar desligada na demonstração (texto livre de anônimo não vai ao
        # modelo externo nem à trilha). Vale aqui, no servidor, e não só no formulário.
        pergunta = None
    # Na demonstração o dia do relatório é o de Brasília, o mesmo do teto diário (o relógio do
    # contêiner pode estar em UTC). Local: o núcleo decide, como sempre.
    extra = {"hoje": hoje_demo()} if cfg.demo_online else {}
    resultado = gerar(
        cfg,
        uf=uf,
        pergunta_extra=(pergunta or "").strip() or None,
        componentes=componentes,
        **extra,
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


class TetoGlobalError(Exception):
    """O teto diário da demonstração foi atingido (mensagem pronta para o visitante)."""


class GeracaoRecenteError(Exception):
    """A sessão terminou uma geração há poucos segundos (clique duplo): nada é gerado."""


# Demonstração: intervalo mínimo entre o fim de uma geração e o próximo envio da mesma sessão.
# O segundo clique de um clique duplo chega como nova execução do script com o envio marcado;
# sem isto, ele geraria (e gastaria) um segundo relatório assim que a trava saísse.
INTERVALO_GERACOES_S = 20.0


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

    def __init__(self, maximo: int = 20, *, publico: bool = False) -> None:
        self._maximo = maximo
        self._publico = publico  # demonstração: a falha guardada não traz infraestrutura
        self._itens: OrderedDict[str, object] = OrderedDict()
        self._fins: dict[str, float] = {}  # quando a sessão terminou a última geração
        self._lock = threading.Lock()

    def guardar(self, sessao: str, valor: object) -> None:
        if isinstance(valor, Exception):
            valor = FalhaTela(classificar(valor, publico=self._publico)[1])
        with self._lock:
            self._itens.pop(sessao, None)
            self._itens[sessao] = valor
            while len(self._itens) > self._maximo:
                antiga, _ = self._itens.popitem(last=False)  # a sessão mais antiga sai
                self._fins.pop(antiga, None)

    def marcar_fim(self, sessao: str, agora: float) -> None:
        with self._lock:
            self._fins[sessao] = agora

    def fim(self, sessao: str) -> float | None:
        with self._lock:
            return self._fins.get(sessao)

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
    conferir_global: Callable[[], str | None] | None = None,
) -> T:
    """Roda `executar` sob a trava global e o teto da sessão; o controle é do servidor.

    Cliques repetidos ou abas paralelas não geram relatórios simultâneos nem passam do teto.
    A tentativa conta mesmo se falhar (o crédito pode ter sido gasto) e a trava sempre sai.
    `guardar` recebe o resultado (ou o erro) ainda aqui dentro, antes de qualquer escrita que o
    Streamlit possa interromper.

    `conferir_global` (só na demonstração) devolve o motivo para não gerar, ou None. Roda com a
    trava pega: conferir e começar são atômicos no processo; entre processos, quem segura é o
    gatilho do banco. Se ela falhar, a exceção sobe e nada é gerado (falha fechada).
    """
    if teto_atingido(estado, teto):
        raise TetoDeRelatoriosError
    if not trava.acquire(blocking=False):
        raise RelatorioEmAndamentoError
    try:
        if conferir_global and (motivo := conferir_global()):
            raise TetoGlobalError(motivo)  # não conta na sessão: nada foi gasto
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
    conferir_global: Callable[[], str | None] | None = None,
    intervalo: float = 0.0,
    relogio: Callable[[], float] | None = None,
) -> None:
    """`executar_controlado` + guarda o resultado da sessão; trava e tetos continuam sendo erro.

    `intervalo` (só na demonstração) recusa com `GeracaoRecenteError`, sem gerar nem contar, o
    envio que chega menos de `intervalo` segundos depois do fim da última geração da sessão.

    Qualquer outra falha vira o resultado da sessão, inclusive a que acontece antes de
    `executar` (contador, spinner): a tela nunca mostra o relatório anterior como se fosse o atual.
    """
    ler_relogio = relogio or _relogio_da_tela
    fim = armazem.fim(sessao)
    if intervalo and fim is not None and ler_relogio() - fim < intervalo:
        raise GeracaoRecenteError
    guardou = False

    def guardar(valor: T | Exception) -> None:
        nonlocal guardou
        armazem.guardar(sessao, valor)
        armazem.marcar_fim(sessao, ler_relogio())  # antes de qualquer escrita do Streamlit
        guardou = True

    try:
        executar_controlado(
            trava, estado, teto, executar, guardar=guardar, conferir_global=conferir_global
        )
    except (RelatorioEmAndamentoError, TetoDeRelatoriosError, TetoGlobalError):
        raise
    except Exception as erro:
        if not guardou:
            armazem.guardar(sessao, erro)


# --- demonstração online: segredos, conferência do ambiente e textos ---

_FALSOS = frozenset({"0", "false", "f", "no", "n", "off"})

# Opções do Streamlit exigidas na demonstração: sem barra de desenvolvedor e sem servir arquivo
# estático (mais o detalhe de erro, abaixo). Comparação com o tipo exato (0 não vale por False).
# Fora daqui: `runOnSave` e `gatherUsageStats`, que a plataforma de hospedagem força e não protegem
# a página; e XSRF e CORS, que lá são geridos pelo proxy dela (no primeiro deploy o valor lido não
# foi o do config.toml). A demo é pública, sem login, sem upload e sem componente que envie dados,
# e o serviço estático segue exigido desligado. Os valores lidos vão para o log (OPCOES_OBSERVADAS).
OPCOES_EXIGIDAS_DEMO: dict[str, object] = {
    "client.toolbarMode": "viewer",
    "server.enableStaticServing": False,
}
# Registradas no log na partida da demo (não são segredos): o dono vê o que a plataforma aplicou.
OPCOES_OBSERVADAS_DEMO: tuple[str, ...] = (
    "server.enableXsrfProtection",
    "server.enableCORS",
    "client.toolbarMode",
    "server.enableStaticServing",
    "client.showErrorDetails",
    "server.runOnSave",
    "browser.gatherUsageStats",
    "runner.fastReruns",
    "server.address",
)
# Detalhe de erro ao visitante: só "none". O legado False (que a plataforma de hospedagem impõe)
# equivale a "stacktrace" no Streamlit e entrega tipo e traceback ao navegador; "full", "type" e
# True também mostram algo. A tela troca a opção para "none" antes desta conferência.
OPCAO_DETALHE_ERRO = "client.showErrorDetails"

MENSAGEM_DEMO_INDISPONIVEL = (
    "A demonstração está indisponível no momento. Tente mais tarde; para rodar localmente, "
    "siga o README."
)
AVISO_PERGUNTA_DEMO = (
    "A pergunta complementar fica desligada na demonstração pública: texto livre iria a um "
    "modelo externo e à trilha de auditoria. Rodando localmente (README), ela está disponível."
)


def config_dos_segredos(segredos: Mapping[str, object]) -> dict[str, object]:
    """Chaves de nível raiz que são campos do Config (sem diferenciar maiúscula); o resto some.

    Tabela aninhada não entra, e nem `_env_file` e afins (não são campos): um segredo não muda
    de onde o Config lê. Nunca registra nem devolve valor em erro.
    """
    campos = Config.model_fields
    return {
        chave.lower(): valor
        for chave, valor in segredos.items()
        if chave.lower() in campos and not isinstance(valor, Mapping)
    }


def demo_pedido(segredos: Mapping[str, object], ambiente: Mapping[str, str]) -> bool:
    """Alguma fonte pede o modo demo? Valor estranho conta como pedido (a mensagem fica genérica).

    Serve para decidir, antes de o Config existir, se um erro pode ser mostrado com detalhe.
    """
    for fonte in (segredos, ambiente):
        for chave, valor in fonte.items():
            if chave.lower() != "demo_online" or isinstance(valor, Mapping):
                continue
            if str(valor).strip().lower() not in _FALSOS:
                return True
    return False


def problemas_ambiente_demo(cfg: Config) -> list[str]:
    """O que impede a demonstração de abrir com esta configuração (vazio = pode).

    Os textos citam o nome da variável, nunca o valor: vão para o log do servidor.
    """
    problemas = []
    if host_local(cfg.pg_host):  # regra única da demo (a mesma da carga)
        problemas.append("PG_HOST aponta para a máquina ou a rede local")
    if cfg.pg_sslmode not in SSLMODES_SEGUROS:
        problemas.append("PG_SSLMODE precisa ser require, verify-ca ou verify-full")
    for papel in ("admin", "etl"):  # a tela nunca recebe credencial de dono nem de carga
        if getattr(cfg, f"pg_senha_{papel}") is not None:
            problemas.append(f"PG_SENHA_{papel.upper()} não pode estar no ambiente da demonstração")
    if cfg.openai_api_key is not None:  # a reserva escaparia do limite de gasto da chave da demo
        problemas.append("OPENAI_API_KEY não pode estar no ambiente da demonstração")
    if cfg.langfuse_public_key is not None or cfg.langfuse_secret_key is not None:
        problemas.append("chaves do LANGFUSE não podem estar no ambiente da demonstração")
    if cfg.llm_provider != "openrouter" or cfg.openrouter_api_key is None:
        problemas.append("a demonstração usa só o OpenRouter, com OPENROUTER_API_KEY definida")
    return problemas


def problemas_opcoes_demo(ler_opcao: Callable[[str], object]) -> list[str]:
    """Opções efetivas do Streamlit (`st.get_option`) que diferem do exigido na demonstração.

    Os textos vão só para o log do dono: nome, valor lido e o exigido (opções não são segredo).
    """
    exigidos: dict[str, object] = {**OPCOES_EXIGIDAS_DEMO, OPCAO_DETALHE_ERRO: "none"}
    regras: dict[str, Callable[[object], bool]] = {
        nome: (lambda valor, esperado=esperado: type(valor) is type(esperado) and valor == esperado)
        for nome, esperado in OPCOES_EXIGIDAS_DEMO.items()
    }
    regras[OPCAO_DETALHE_ERRO] = _detalhe_de_erro_escondido
    problemas = []
    for nome, aceita in regras.items():
        try:
            valor = ler_opcao(nome)
        except Exception:
            problemas.append(f"{nome} ilegível")
            continue
        if not aceita(valor):
            problemas.append(f"{nome} lido={valor!r} (exigido {exigidos[nome]!r})")
    return problemas


def valores_das_opcoes(ler_opcao: Callable[[str], object]) -> dict[str, str]:
    """`repr` de cada opção observada (para o log); leitura que falha vira "ilegível"."""
    valores = {}
    for nome in OPCOES_OBSERVADAS_DEMO:
        try:
            valores[nome] = repr(ler_opcao(nome))
        except Exception:
            valores[nome] = "ilegível"
    return valores


def _detalhe_de_erro_escondido(valor: object) -> bool:
    return type(valor) is str and valor == "none"


def _data(pacote: PacoteDemo | None) -> str:
    if pacote is None or pacote.data_referencia is None:
        return "indisponível"
    return pacote.data_referencia.strftime("%d/%m/%Y")


def texto_banner(uso: UsoDiario | None, pacote: PacoteDemo | None) -> str:
    """Aviso do topo da demonstração (Markdown; só números e datas vindos do banco)."""
    if uso is None:
        limite = (
            "há um limite diário de relatórios para todos os visitantes "
            "(uso de hoje indisponível agora"
        )
    else:
        limite = (
            f"há um limite diário de {uso.relatorios_dia} relatórios para todos os visitantes "
            f"(hoje: {uso.relatorios} de {uso.relatorios_dia}"
        )
    return (
        "**Demonstração pública.** Os números vêm de agregados por UF e dia, sem nenhum dado "
        f"individual (data de referência: {_data(pacote)}). Cada relatório usa crédito de modelo "
        f"de linguagem do autor; {limite}; renova à meia-noite, horário de Brasília). "
        f"{AVISO_PERGUNTA_DEMO} A primeira visita depois de um tempo sem uso pode demorar alguns "
        "segundos."
    )


def texto_rodape(pacote: PacoteDemo | None, busca: str) -> str:
    """Proveniência do pacote e modo da busca; o que vem do banco passa por lista fechada."""
    sha = pacote.sha256.lower() if pacote else ""
    sha = sha[:12] if re.fullmatch(r"[0-9a-f]{12,64}", sha) else "desconhecido"
    versao = re.sub(r"[^0-9A-Za-z.-]", "", pacote.versao_codigo)[:40] if pacote else ""
    metodo = "nome exato + texto completo" if busca == "texto" else "nome exato + similaridade"
    return (
        f"Pacote de dados {sha}, gerado pelo código {versao or 'desconhecido'}. "
        f"Busca do dicionário: {metodo}."
    )


# --- demonstração: pasta dos relatórios, limites do dicionário e relatório de exemplo ---

PASTA_RELATORIOS_DEMO = Path(tempfile.gettempdir()) / "srag-relatorios"
# Dicionário na demonstração: cada consulta abre conexão com o banco gerenciado e o mantém
# acordado (cota mensal de computação). Intervalo por sessão + teto global por minuto.
INTERVALO_DICIONARIO_S = 3.0
CONSULTAS_DICIONARIO_POR_MINUTO = 30
CHAVE_ULTIMA_CONSULTA = "ultima_consulta_dicionario"  # em st.session_state
EXEMPLO_RELATORIO = Path("relatorios/exemplo/relatorio.html")  # versionado, a partir da raiz


def ajustar_config_demo(cfg: Config) -> Config:
    """Na demonstração, sem `PASTA_RELATORIOS` explícita, grava na pasta temporária do sistema.

    A pasta do repositório na plataforma pode não ser gravável (o relatório terminaria em erro
    depois de gastar crédito), e os relatórios dos visitantes não precisam sobreviver ao processo.
    """
    if not cfg.demo_online or "pasta_relatorios" in cfg.model_fields_set:
        return cfg
    return cfg.model_copy(update={"pasta_relatorios": PASTA_RELATORIOS_DEMO})


def problema_pasta(pasta: Path) -> str | None:
    """Cria a pasta e prova que dá para gravar nela; o texto não traz o caminho."""
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=pasta, prefix=".sonda-"):
            pass
    except OSError:
        return "pasta dos relatórios não gravável"
    return None


def intervalo_respeitado(estado: MutableMapping, agora: float, intervalo: float) -> bool:
    """Intervalo mínimo entre consultas da mesma sessão; a recusada não reinicia a contagem."""
    ultima = estado.get(CHAVE_ULTIMA_CONSULTA)
    if ultima is not None and agora - ultima < intervalo:
        return False
    estado[CHAVE_ULTIMA_CONSULTA] = agora
    return True


relogio: Callable[[], float] = time.monotonic  # a tela lê o relógio por aqui (dublê nos testes)


def _relogio_da_tela() -> float:
    return relogio()  # lido na hora da chamada: os testes trocam `tela.relogio`


class LimiteConsultas:
    """Teto global de consultas por minuto (janela deslizante de 60 s), seguro entre threads."""

    def __init__(self, por_minuto: int, *, relogio: Callable[[], float] = time.monotonic) -> None:
        self._por_minuto = por_minuto
        self._relogio = relogio
        self._momentos: deque[float] = deque()
        self._lock = threading.Lock()

    def permitir(self) -> bool:
        with self._lock:
            agora = self._relogio()
            while self._momentos and self._momentos[0] <= agora - 60:
                self._momentos.popleft()
            if len(self._momentos) >= self._por_minuto:
                return False  # recusa não ocupa vaga
            self._momentos.append(agora)
            return True


def ler_exemplo(caminho: Path) -> str | None:
    """HTML do relatório de exemplo versionado (mostrado no iframe isolado), ou None."""
    try:
        return caminho.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
