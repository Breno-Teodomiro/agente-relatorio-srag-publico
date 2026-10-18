"""Helpers da tela (sem Streamlit): geração pelo núcleo, nível da situação e leitura do HTML."""

import gc
import threading
import weakref
from pathlib import Path
from uuid import uuid4

import pytest

from srag_agente import tela
from srag_agente.nucleo import ResultadoExecucao
from srag_agente.relatorio.montar import ArquivosRelatorio


def _resultado(tmp_path, status="ok") -> ResultadoExecucao:
    html = tmp_path / "relatorio.html"
    html.write_text("<html>relatório</html>", encoding="utf-8")
    auditoria = tmp_path / "auditoria.jsonl"
    auditoria.write_text('{"etapa": "nucleo"}\n', encoding="utf-8")
    arquivos = ArquivosRelatorio(pasta=tmp_path, html=html, markdown=tmp_path / "r.md",
                                 auditoria=auditoria)  # fmt: skip
    return ResultadoExecucao(id_execucao=uuid4(), status=status, arquivos=arquivos,
                             tokens_entrada=10, tokens_saida=5, custo_usd=0.0123)  # fmt: skip


def test_gera_pelo_nucleo_com_uf_e_pergunta_sem_espacos_nas_pontas(tmp_path):
    chamadas = []

    def falso(cfg, *, uf, pergunta_extra, componentes):
        chamadas.append((uf, pergunta_extra, componentes))
        return _resultado(tmp_path)

    r = tela.gerar_para_tela("cfg", "SP", "  Como está a UTI? ", gerar=falso, componentes="c")
    assert chamadas == [("SP", "Como está a UTI?", "c")]
    assert r.html == "<html>relatório</html>"
    assert r.trilha == '{"etapa": "nucleo"}\n'
    assert r.custo_usd == 0.0123


def test_pergunta_em_branco_vira_sem_pergunta(tmp_path):
    chamadas = []

    def falso(cfg, *, uf, pergunta_extra, componentes):
        chamadas.append(pergunta_extra)
        return _resultado(tmp_path)

    tela.gerar_para_tela("cfg", "BR", "   ", gerar=falso, componentes=None)
    assert chamadas == [None]


@pytest.mark.parametrize(
    "status,nivel,rotulo",
    [("ok", "sucesso", "concluído"), ("parcial", "aviso", "parcial"),
     ("revisao_humana", "aviso", "revisão humana necessária"), ("erro", "erro", "erro")],
)  # fmt: skip
def test_situacao_tem_nivel_e_rotulo_em_portugues(tmp_path, status, nivel, rotulo):
    r = tela.gerar_para_tela("cfg", "BR", None, gerar=lambda *a, **k: _resultado(tmp_path, status),
                             componentes=None)  # fmt: skip
    assert (r.nivel, r.situacao) == (nivel, rotulo)


def test_url_isolada_leva_o_html_em_data_url_que_volta_igual():
    import base64

    url = tela.url_isolada("<p>relatório \u2014 açaí</p>")
    prefixo = "data:text/html;base64,"
    assert url.startswith(prefixo)
    assert base64.b64decode(url[len(prefixo) :]).decode("utf-8") == "<p>relatório \u2014 açaí</p>"


# --- trava global e teto por sessão (controle no servidor, não no botão) ---


def test_trava_ocupada_recusa_sem_executar():
    trava = threading.Lock()
    executou = []
    with trava, pytest.raises(tela.RelatorioEmAndamentoError):
        tela.executar_controlado(trava, {}, 5, lambda: executou.append(1))
    assert executou == []


def test_libera_a_trava_quando_a_geracao_falha():
    trava = threading.Lock()

    def falha():
        raise RuntimeError("banco fora")

    with pytest.raises(RuntimeError):
        tela.executar_controlado(trava, {}, 5, falha)
    assert trava.acquire(blocking=False)  # liberada: a próxima tentativa passa


def test_devolve_o_resultado_e_conta_o_uso_mesmo_quando_falha():
    trava, estado = threading.Lock(), {}
    assert tela.executar_controlado(trava, estado, 5, lambda: "ok") == "ok"
    with pytest.raises(RuntimeError):
        tela.executar_controlado(trava, estado, 5, lambda: (_ for _ in ()).throw(RuntimeError()))
    assert estado[tela.CHAVE_USADOS] == 2  # falha também gasta crédito: conta


def test_teto_por_sessao_estoura_sem_executar_nem_prender_a_trava():
    trava, estado = threading.Lock(), {tela.CHAVE_USADOS: 2}
    executou = []
    with pytest.raises(tela.TetoDeRelatoriosError):
        tela.executar_controlado(trava, estado, 2, lambda: executou.append(1))
    assert executou == []
    assert trava.acquire(blocking=False)
    assert tela.teto_atingido(estado, 2) and not tela.teto_atingido(estado, 3)


# --- resultado no servidor: sobrevive à execução interrompida pelo Streamlit ---


def test_resultado_e_guardado_mesmo_se_o_chamador_original_for_interrompido():
    trava, armazem = threading.Lock(), tela.ArmazemResultados(maximo=3)

    class Parada(BaseException):  # como a StopException do Streamlit
        pass

    def guardar(valor):
        armazem.guardar("s1", valor)
        raise Parada  # a escrita "do chamador" é interrompida depois de guardada

    with pytest.raises(Parada):
        tela.executar_controlado(trava, {}, 5, lambda: "relatório", guardar=guardar)
    assert armazem.ler("s1") == "relatório"
    assert trava.acquire(blocking=False)  # a trava saiu mesmo com BaseException


def test_libera_a_trava_com_baseexception_na_geracao():
    trava = threading.Lock()

    def interrompe():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        tela.executar_controlado(trava, {}, 5, interrompe)
    assert trava.acquire(blocking=False)


def test_erro_da_geracao_tambem_e_guardado_para_a_tela():
    trava, armazem = threading.Lock(), tela.ArmazemResultados(maximo=3)
    erro = RuntimeError("banco fora")

    def falha():
        raise erro

    with pytest.raises(RuntimeError):
        tela.executar_controlado(trava, {}, 5, falha, guardar=lambda v: armazem.guardar("s1", v))
    guardado = armazem.ler("s1")
    assert isinstance(guardado, tela.FalhaTela)
    assert "banco fora" in guardado.mensagem


def test_trava_ocupada_ou_teto_nao_guardam_nada():
    armazem = tela.ArmazemResultados(maximo=3)
    trava = threading.Lock()
    with trava, pytest.raises(tela.RelatorioEmAndamentoError):
        tela.executar_controlado(trava, {}, 5, lambda: "x",
                                 guardar=lambda v: armazem.guardar("s", v))  # fmt: skip
    with pytest.raises(tela.TetoDeRelatoriosError):
        tela.executar_controlado(trava, {tela.CHAVE_USADOS: 5}, 5, lambda: "x",
                                 guardar=lambda v: armazem.guardar("s", v))  # fmt: skip
    assert armazem.ler("s") is None


def test_armazem_guarda_so_o_ultimo_por_sessao_e_descarta_as_mais_antigas():
    armazem = tela.ArmazemResultados(maximo=2)
    armazem.guardar("a", 1)
    armazem.guardar("a", 2)  # substitui, não acumula
    armazem.guardar("b", 3)
    armazem.guardar("c", 4)  # estoura: sai a sessão mais antiga ("a")
    assert [armazem.ler(s) for s in "abc"] == [None, 3, 4]


def test_ler_renova_a_posicao_e_o_leitor_sobrevive_a_gravacoes_de_outras_sessoes():
    armazem = tela.ArmazemResultados(maximo=3)
    for sessao in ("leitor", "x", "y"):
        armazem.guardar(sessao, sessao)
    assert armazem.ler("leitor") == "leitor"  # renova: "x" passa a ser a mais antiga
    armazem.guardar("z", "z")
    armazem.guardar("w", "w")  # saem "x" e "y"
    assert [armazem.ler(s) for s in ("leitor", "x", "y")] == ["leitor", None, None]


def test_armazem_nao_guarda_a_excecao_nem_o_traceback():
    armazem = tela.ArmazemResultados(maximo=2)

    class Pergunta:  # variável local do frame que falhou: o traceback a manteria viva
        pass

    referencias = []

    def falha():
        pergunta = Pergunta()
        referencias.append(weakref.ref(pergunta))
        raise RuntimeError("banco fora")

    try:
        falha()
    except RuntimeError as erro:
        armazem.guardar("s", erro)
    gc.collect()
    assert referencias[0]() is None  # nada no armazém segura frames nem variáveis locais
    valor = armazem.ler("s")
    assert isinstance(valor, tela.FalhaTela) and not hasattr(valor, "__traceback__")


# --- falha antes do `guardar` (spinner, contador): a tela não pode mostrar o relatório antigo ---


class _EstadoQuebrado(dict):
    def __setitem__(self, chave, valor):
        raise RuntimeError("session_state fora")


def test_falha_antes_de_executar_substitui_o_resultado_antigo_pela_falha():
    armazem, trava = tela.ArmazemResultados(maximo=3), threading.Lock()
    armazem.guardar("s", "relatório antigo")
    tela.executar_e_guardar(armazem, "s", trava, _EstadoQuebrado(), 5, lambda: "novo")
    valor = armazem.ler("s")
    assert isinstance(valor, tela.FalhaTela)
    assert trava.acquire(blocking=False)


def test_executar_e_guardar_guarda_o_resultado_e_repassa_trava_e_teto():
    armazem, trava = tela.ArmazemResultados(maximo=3), threading.Lock()
    tela.executar_e_guardar(armazem, "s", trava, {}, 5, lambda: "ok")
    assert armazem.ler("s") == "ok"
    with trava, pytest.raises(tela.RelatorioEmAndamentoError):
        tela.executar_e_guardar(armazem, "s", trava, {}, 5, lambda: "x")
    with pytest.raises(tela.TetoDeRelatoriosError):
        tela.executar_e_guardar(armazem, "s", trava, {tela.CHAVE_USADOS: 5}, 5, lambda: "x")
    assert armazem.ler("s") == "ok"


# --- regressão: a tela nunca injeta HTML cru na página ---

_RAIZ_SRC = Path(__file__).resolve().parents[2] / "src"
_PROIBIDOS = ("unsafe_allow_html", "st.html(", "components.v1", "components.html")


def test_src_nao_injeta_html_cru_na_pagina():
    achados = []
    for arquivo in _RAIZ_SRC.rglob("*.py"):
        if arquivo.name == "test_tela.py":
            continue
        for n, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
            if linha.lstrip().startswith("#"):
                continue
            if any(p in linha for p in _PROIBIDOS):
                achados.append(f"{arquivo.name}:{n}: {linha.strip()}")
            # st.iframe só com a URL `data:` de url_isolada: HTML cru iria para srcdoc
            # com allow-same-origin e alcançaria a página.
            if "st.iframe(" in linha and "url_isolada(" not in linha:
                achados.append(f"{arquivo.name}:{n}: {linha.strip()}")
    assert achados == []
