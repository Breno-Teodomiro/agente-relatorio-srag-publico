"""Tela local (Streamlit): relatório de SRAG e consulta ao dicionário de dados.

Roda só em 127.0.0.1 (`make app`). O que vem do LLM ou de notícia nunca entra como HTML da
página: o relatório (já escapado pelo gerador) aparece num iframe de origem própria (URL
`data:`) e o resto é texto.
"""

import threading
from typing import get_args

import streamlit as st
from pydantic import ValidationError
from streamlit.runtime.scriptrunner import get_script_run_ctx

from srag_agente import nucleo, tela
from srag_agente.config import Config
from srag_agente.erros import classificar, mensagem_configuracao
from srag_agente.ferramentas.registro import UF
from srag_agente.guardrails.entrada import LIMITE_PERGUNTA
from srag_agente.rag import consulta
from srag_agente.rag.embeddings import criar_embedder
from srag_agente.tela import (
    ArmazemResultados,
    FalhaTela,
    RelatorioEmAndamentoError,
    RelatorioTela,
    TetoDeRelatoriosError,
    gerar_para_tela,
    teto_atingido,
    url_isolada,
)

ALTURA_RELATORIO = 1400
ENDERECO_LOCAL = "127.0.0.1"
ATUALIZA_A_CADA_S = 3  # a tela confere a trava enquanto outra execução gera (sem custo de LLM)


@st.cache_resource
def _config() -> Config:
    return Config()


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
    return ArmazemResultados()  # último resultado por sessão, no servidor


def _sessao() -> str:
    ctx = get_script_run_ctx()
    return ctx.session_id if ctx else "sem-sessao"


def _mostrar_erro(erro: Exception) -> None:
    st.error(classificar(erro)[1])  # sem traceback, sem input cru, dado pessoal mascarado


def _mostrar_relatorio(resultado: RelatorioTela) -> None:
    aviso = {"sucesso": st.success, "aviso": st.warning, "erro": st.error}[resultado.nivel]
    aviso(f"Situação: {resultado.situacao} (custo estimado US$ {resultado.custo_usd:.4f})")
    st.iframe(url_isolada(resultado.html), height=ALTURA_RELATORIO)
    st.download_button("Baixar a trilha de auditoria", resultado.trilha, "auditoria.jsonl")


def _gerar(uf: str, pergunta: str) -> None:
    cfg = _config()
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
            )
    except RelatorioEmAndamentoError:
        st.warning("Já há um relatório em geração.")
    except TetoDeRelatoriosError:
        st.warning(f"Limite de {cfg.max_relatorios_sessao} relatórios por sessão atingido.")


def _exibir_resultado() -> None:
    valor = _resultados().ler(_sessao())
    if isinstance(valor, FalhaTela):
        st.error(valor.mensagem)
    elif valor is not None:
        try:
            _mostrar_relatorio(valor)
        except Exception as erro:  # iframe e download também ficam sob o tratamento limpo
            _mostrar_erro(erro)


def _painel_resultado() -> None:
    """Mostra o resultado da sessão; enquanto a trava está ocupada, se atualiza sozinho."""
    ocupado = _trava_geracao().locked()

    def corpo() -> None:
        if ocupado and not _trava_geracao().locked():
            st.rerun()  # a geração terminou: redesenha a página inteira com o resultado
        if ocupado:
            st.info("Outro relatório está em geração; tente de novo ao terminar.")
        _exibir_resultado()  # o da própria sessão, se houver

    st.fragment(run_every=ATUALIZA_A_CADA_S if ocupado else None)(corpo)()


def _aba_relatorio() -> None:
    esgotado = teto_atingido(st.session_state, _config().max_relatorios_sessao)
    with st.form("relatorio"):
        uf = st.selectbox("UF", get_args(UF), help="BR é o país inteiro.")
        pergunta = st.text_input("Pergunta complementar (opcional)", max_chars=LIMITE_PERGUNTA)
        enviar = st.form_submit_button("Gerar relatório", disabled=esgotado)  # só complemento
    if enviar:
        _gerar(uf, pergunta)
    _painel_resultado()


def _aba_dicionario() -> None:
    with st.form("dicionario"):
        pergunta = st.text_input("Campo ou pergunta", max_chars=LIMITE_PERGUNTA,
                                 placeholder="Ex.: o que é VACINA_COV?")  # fmt: skip
        enviar = st.form_submit_button("Consultar")
    if not enviar:
        return
    try:
        consulta.validar_pergunta(pergunta)
        with consulta.abrir_busca(_config(), _embedder()) as busca:
            respostas = consulta.responder(busca, pergunta)
    except consulta.PerguntaInvalidaError as erro:
        st.warning(str(erro))
        return
    except Exception as erro:
        _mostrar_erro(erro)
        return
    for r in respostas:
        st.markdown(f"**{r.campo or 'campo não identificado'}** · página {r.pagina} · {r.origem}")
        st.text(r.texto)  # texto puro: o conteúdo do dicionário nunca vira HTML


def main() -> None:
    st.set_page_config(page_title="Agente SRAG", layout="wide")
    st.title("Agente SRAG")
    if st.get_option("server.address") != ENDERECO_LOCAL:
        st.error(f"Esta tela só roda em {ENDERECO_LOCAL} (use `make app`).")
        st.stop()
    try:
        _config()
    except ValidationError as erro:
        st.error(mensagem_configuracao(erro))
        return
    except Exception as erro:
        _mostrar_erro(erro)
        return
    relatorio, dicionario = st.tabs(["Relatório", "Dicionário de dados"])
    with relatorio:
        _aba_relatorio()
    with dicionario:
        _aba_dicionario()


main()
