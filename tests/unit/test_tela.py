"""Helpers da tela (sem Streamlit): geração pelo núcleo, nível da situação e leitura do HTML."""

import threading
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
