"""Tela (Streamlit): relatório de SRAG e consulta ao dicionário de dados.

Local (padrão): roda só em 127.0.0.1 (`make app`). Demonstração online (`DEMO_ONLINE=1`): abre só
se o ambiente passar nas conferências de `tela` e do banco; tem teto diário, pergunta
complementar desligada e erros sem detalhe de infraestrutura. Em qualquer modo, o que vem do LLM
ou de notícia nunca entra como HTML da página: o relatório (já escapado pelo gerador) aparece num
iframe de origem própria (URL `data:`) e o resto é texto.
"""

import logging
import os
import sys
import threading
from typing import get_args

import streamlit as st
from pydantic import ValidationError
from streamlit.runtime.scriptrunner import get_script_run_ctx

from srag_agente import nucleo, tela
from srag_agente.config import RAIZ_PROJETO, Config
from srag_agente.demo import teto
from srag_agente.erros import classificar, mensagem_configuracao, registrar_falha
from srag_agente.ferramentas.registro import UF
from srag_agente.guardrails.entrada import LIMITE_PERGUNTA
from srag_agente.rag import consulta
from srag_agente.rag.embeddings import criar_embedder
from srag_agente.tela import (
    ArmazemResultados,
    FalhaTela,
    GeracaoRecenteError,
    RelatorioEmAndamentoError,
    RelatorioTela,
    TetoDeRelatoriosError,
    TetoGlobalError,
    gerar_para_tela,
    teto_atingido,
    url_isolada,
)

ALTURA_RELATORIO = 1400
ENDERECO_LOCAL = "127.0.0.1"
ATUALIZA_A_CADA_S = 3  # a tela confere a trava enquanto outra execução gera (sem custo de LLM)
USO_INDISPONIVEL = (
    "Não foi possível conferir o limite diário agora; a geração fica bloqueada até o banco "
    "responder. A aba Dicionário de dados continua disponível."
)

log = logging.getLogger(__name__)


def _segredos() -> dict:
    """Segredos da plataforma (nível raiz). Local, sem `.streamlit/secrets.toml`: nenhum."""
    try:
        return dict(st.secrets)
    except FileNotFoundError:  # StreamlitSecretNotFoundError é subclasse dela
        return {}


@st.cache_resource
def _config() -> Config:
    # Argumento do construtor vence variável de ambiente e `.env`: sem segredos, nada muda.
    # Na demonstração, sem pasta configurada, os relatórios vão para a pasta temporária.
    return tela.ajustar_config_demo(Config(**tela.config_dos_segredos(_segredos())))


@st.cache_resource
def _embedder():
    return criar_embedder(_config().pasta_cache_modelos)


@st.cache_resource
def _componentes() -> nucleo.Componentes:
    return nucleo.componentes_reais(_config())


@st.cache_resource
def _trava_geracao() -> threading.Lock:
    return threading.Lock()  # uma só para o servidor: um relatório por vez


@st.cache_resource
def _resultados() -> ArmazemResultados:
    # último resultado por sessão, no servidor; na demonstração, falha sem infraestrutura
    return ArmazemResultados(publico=_config().demo_online)


def _sessao() -> str:
    ctx = get_script_run_ctx()
    return ctx.session_id if ctx else "sem-sessao"


def _mostrar_erro(erro: Exception, publico: bool) -> None:
    st.error(classificar(erro, publico=publico)[1])  # sem traceback, sem input cru


def _exigir_endereco_local() -> None:
    if st.get_option("server.address") != ENDERECO_LOCAL:
        st.error(f"Esta tela só roda em {ENDERECO_LOCAL} (use `make app`).")
        st.stop()


# --- demonstração online ---


@st.cache_resource
def _log_demo() -> None:
    """Log do servidor (visível só para o dono do app): só textos nossos, números e tipos."""
    raiz = logging.getLogger("srag_agente")
    raiz.setLevel(logging.INFO)
    if not raiz.handlers:
        saida = logging.StreamHandler(sys.stderr)
        saida.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        raiz.addHandler(saida)


def _registrar_memoria(momento: str) -> None:
    log.info("memória (%s): %s", momento, teto.memoria_kb())


@st.cache_resource
def _memoria_na_partida() -> None:
    _registrar_memoria("partida")  # uma vez por processo


class _BancoDaDemoIncompletoError(RuntimeError):
    """O banco da demonstração não tem o gatilho do teto ou os limites."""


@st.cache_resource(ttl=600, show_spinner=False)
def _banco_pronto() -> bool:
    # Exceção não fica no cache: banco hibernando ou incompleto é conferido de novo na visita
    # seguinte; o sucesso vale por 10 minutos.
    problemas = teto.prontidao_banco_demo(_config())
    if problemas:
        raise _BancoDaDemoIncompletoError("; ".join(problemas))
    return True


def _esconder_detalhe_de_erro() -> None:
    # A plataforma impõe o legado False, que o Streamlit trata como "stacktrace" (tipo e traceback
    # no navegador). O script troca para "none" antes de tudo; a conferência exige "none".
    st.set_option("client.showErrorDetails", "none")


@st.cache_resource
def _problemas_do_ambiente() -> tuple[str, ...]:
    # Uma vez por processo: a conferência resolve DNS e grava um arquivo-sonda.
    cfg = _config()
    problemas = tela.problemas_ambiente_demo(cfg) + tela.problemas_opcoes_demo(st.get_option)
    if pasta := tela.problema_pasta(cfg.pasta_relatorios):
        problemas.append(pasta)  # sem pasta gravável o relatório falharia depois de gastar
    return tuple(problemas)


def _demo_pronta(cfg: Config) -> bool:
    """Confere tudo na partida; qualquer falta recusa (falha fechada). O motivo vai só ao log."""
    problemas = list(_problemas_do_ambiente())
    if not problemas:
        try:
            _banco_pronto()
        except _BancoDaDemoIncompletoError as erro:
            problemas = [str(erro)]
        except Exception as erro:  # o texto pode ter host e usuário: só a referência
            problemas = [f"banco inacessível (referência {registrar_falha(erro)})"]
    if problemas:
        log.error("demonstração recusada na partida: %s", "; ".join(problemas))
        st.error(tela.MENSAGEM_DEMO_INDISPONIVEL)
        return False
    _memoria_na_partida()
    return True


@st.cache_data(ttl=30, show_spinner=False)
def _uso_hoje() -> teto.UsoDiario:
    # Só para exibir e desabilitar o botão; a decisão que vale é a de dentro da trava (fresca).
    return teto.uso_de_hoje(_config())


@st.cache_data(ttl=600, show_spinner=False)
def _pacote() -> teto.PacoteDemo | None:
    return teto.pacote_carregado(_config())


@st.cache_resource
def _exemplo() -> str | None:
    return tela.ler_exemplo(RAIZ_PROJETO / tela.EXEMPLO_RELATORIO)


@st.cache_resource
def _limite_dicionario() -> tela.LimiteConsultas:
    return tela.LimiteConsultas(tela.CONSULTAS_DICIONARIO_POR_MINUTO)  # um para o servidor


@st.cache_data(max_entries=256, ttl=3600, show_spinner=False)
def _respostas_dicionario(pergunta: str) -> list[consulta.Resposta]:
    # Demonstração: pergunta repetida não abre conexão nem acorda o banco.
    cfg = _config()
    embedder = _embedder() if cfg.busca_dicionario == "vetor" else None
    with consulta.abrir_busca(cfg, embedder) as busca:
        return consulta.responder(busca, pergunta)


def _ler_para_exibir(ler):
    try:
        return ler()
    except Exception as erro:
        registrar_falha(erro)
        return None


# --- abas ---


def _mostrar_relatorio(resultado: RelatorioTela) -> None:
    aviso = {"sucesso": st.success, "aviso": st.warning, "erro": st.error}[resultado.nivel]
    aviso(f"Situação: {resultado.situacao} (custo estimado US$ {resultado.custo_usd:.4f})")
    st.iframe(url_isolada(resultado.html), height=ALTURA_RELATORIO)
    st.download_button("Baixar a trilha de auditoria", resultado.trilha, "auditoria.jsonl")


def _gerar(cfg: Config, uf: str, pergunta: str | None) -> None:
    conferir = (lambda: teto.conferir_teto(cfg)) if cfg.demo_online else None
    try:
        with st.spinner("Gerando o relatório (consulta o banco, notícias e o modelo)..."):
            # o resultado (ou a falha) é guardado no servidor, dentro da execução controlada: se o
            # Streamlit interromper este script (novo clique), a escrita no session_state falharia
            tela.executar_e_guardar(
                _resultados(),
                _sessao(),
                _trava_geracao(),
                st.session_state,
                cfg.max_relatorios_sessao,
                lambda: gerar_para_tela(cfg, uf, pergunta, componentes=_componentes()),
                conferir_global=conferir,
                intervalo=tela.INTERVALO_GERACOES_S if cfg.demo_online else 0.0,
            )
    except RelatorioEmAndamentoError:
        st.warning("Já há um relatório em geração.")
    except TetoDeRelatoriosError:
        st.warning(f"Limite de {cfg.max_relatorios_sessao} relatórios por sessão atingido.")
    except TetoGlobalError as aviso:
        st.info(f"{aviso} {teto.COMPLEMENTO_TETO}")
        _mostrar_exemplo()  # no mesmo ciclo do clique, sem esperar a próxima execução
    except GeracaoRecenteError:
        st.info("O relatório acabou de ser gerado; aguarde alguns segundos para pedir outro.")
    finally:
        if cfg.demo_online:
            _uso_hoje.clear()  # o banner e o botão passam a ver o uso novo
            _registrar_memoria("depois do relatório")


def _exibir_resultado(publico: bool) -> None:
    valor = _resultados().ler(_sessao())
    if isinstance(valor, FalhaTela):
        st.error(valor.mensagem)
    elif valor is not None:
        try:
            _mostrar_relatorio(valor)
        except Exception as erro:  # iframe e download também ficam sob o tratamento limpo
            _mostrar_erro(erro, publico)


def _painel_resultado(publico: bool) -> None:
    """Mostra o resultado da sessão; enquanto a trava está ocupada, se atualiza sozinho."""
    ocupado = _trava_geracao().locked()

    def corpo() -> None:
        if ocupado and not _trava_geracao().locked():
            st.rerun()  # a geração terminou: redesenha a página inteira com o resultado
        if ocupado:
            st.info("Outro relatório está em geração; tente de novo ao terminar.")
        _exibir_resultado(publico)  # o da própria sessão, se houver

    st.fragment(run_every=ATUALIZA_A_CADA_S if ocupado else None)(corpo)()


def _bloqueio_do_dia(cfg: Config) -> str | None:
    """Texto que bloqueia o botão na demonstração (teto ou uso ilegível); None se pode gerar."""
    uso = _ler_para_exibir(_uso_hoje)
    if uso is None:
        return USO_INDISPONIVEL
    motivo = teto.motivo_para(cfg, uso)
    return f"{motivo} {teto.COMPLEMENTO_TETO}" if motivo else None


def _mostrar_exemplo() -> None:
    html = _exemplo()
    if html is None:
        st.caption("O relatório de exemplo está no repositório (relatorios/exemplo).")
        return
    with st.expander("Relatório de exemplo (gerado localmente com a base completa)"):
        st.iframe(url_isolada(html), height=ALTURA_RELATORIO)  # mesmo isolamento do gerado


def _consulta_liberada(cfg: Config) -> bool:
    """Demonstração: intervalo mínimo por sessão e teto global por minuto no dicionário."""
    if not cfg.demo_online:
        return True
    if not tela.intervalo_respeitado(st.session_state, tela.relogio(),
                                     tela.INTERVALO_DICIONARIO_S):  # fmt: skip
        st.warning("Aguarde alguns segundos entre uma consulta e outra.")
        return False
    if not _limite_dicionario().permitir():
        st.warning("Muitas consultas ao dicionário neste minuto; tente de novo em instantes.")
        return False
    return True


def _aba_relatorio(cfg: Config) -> None:
    esgotado = teto_atingido(st.session_state, cfg.max_relatorios_sessao)
    if cfg.demo_online and (bloqueio := _bloqueio_do_dia(cfg)):
        st.info(bloqueio)  # a trava bloqueia a ação, nunca a visão: o resto da tela segue
        esgotado = True
        _mostrar_exemplo()
    with st.form("relatorio"):
        uf = st.selectbox("UF", get_args(UF), help="BR é o país inteiro.")
        if cfg.demo_online:
            st.caption(tela.AVISO_PERGUNTA_DEMO)
            pergunta = None
        else:
            pergunta = st.text_input("Pergunta complementar (opcional)", max_chars=LIMITE_PERGUNTA)
        enviar = st.form_submit_button("Gerar relatório", disabled=esgotado)  # só complemento
    if enviar:
        _gerar(cfg, uf, pergunta)
    _painel_resultado(cfg.demo_online)


def _aba_dicionario(cfg: Config) -> None:
    with st.form("dicionario"):
        pergunta = st.text_input("Campo ou pergunta", max_chars=LIMITE_PERGUNTA,
                                 placeholder="Ex.: o que é VACINA_COV?")  # fmt: skip
        enviar = st.form_submit_button("Consultar")
    if not enviar:
        return
    try:
        limpa = consulta.validar_pergunta(pergunta)
        if not _consulta_liberada(cfg):
            return
        if cfg.demo_online:
            respostas = _respostas_dicionario(" ".join(limpa.split()))
        else:
            # no modo `texto` o modelo de embeddings nunca é carregado (memória da plataforma)
            embedder = _embedder() if cfg.busca_dicionario == "vetor" else None
            with consulta.abrir_busca(cfg, embedder) as busca:
                respostas = consulta.responder(busca, pergunta)
    except consulta.PerguntaInvalidaError as erro:
        st.warning(str(erro))
        return
    except Exception as erro:
        _mostrar_erro(erro, cfg.demo_online)
        return
    for r in respostas:
        st.markdown(f"**{r.campo or 'campo não identificado'}** · página {r.pagina} · {r.origem}")
        st.text(r.texto)  # texto puro: o conteúdo do dicionário nunca vira HTML


def main() -> None:
    st.set_page_config(page_title="Agente SRAG", layout="wide")
    st.title("Agente SRAG")
    # Pedido de demo em qualquer fonte: erro de configuração não mostra campo ao visitante.
    pedido = tela.demo_pedido(_segredos(), os.environ)
    if pedido:
        _esconder_detalhe_de_erro()  # antes de montar o Config: nada de traceback ao visitante
        _log_demo()
    else:
        _exigir_endereco_local()  # local: a regra de sempre, antes de tudo
    try:
        cfg = _config()
    except ValidationError as erro:
        if pedido:
            log.error("configuração da demonstração inválida: %s", mensagem_configuracao(erro))
            st.error(tela.MENSAGEM_DEMO_INDISPONIVEL)
        else:
            st.error(mensagem_configuracao(erro))
        return
    except Exception as erro:
        _mostrar_erro(erro, publico=pedido)
        return
    pacote = None
    if cfg.demo_online:
        _esconder_detalhe_de_erro()
        _log_demo()
        if not _demo_pronta(cfg):
            st.stop()
        pacote = _ler_para_exibir(_pacote)
        st.info(tela.texto_banner(_ler_para_exibir(_uso_hoje), pacote))
    else:
        _exigir_endereco_local()  # o pedido pode ter vindo de fonte que o Config não usou
    relatorio, dicionario = st.tabs(["Relatório", "Dicionário de dados"])
    with relatorio:
        _aba_relatorio(cfg)
    with dicionario:
        _aba_dicionario(cfg)
    if cfg.demo_online:
        st.caption(tela.texto_rodape(pacote, cfg.busca_dicionario))


if __name__ == "__main__":  # o Streamlit roda o arquivo como __main__ (make app e AppTest)
    main()
