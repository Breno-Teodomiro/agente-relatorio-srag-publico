"""Helpers da tela (sem Streamlit): geração pelo núcleo, nível da situação e leitura do HTML."""

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
