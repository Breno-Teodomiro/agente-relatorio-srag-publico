"""A tela (app.py) sob `AppTest`: "ocupado" -> "resultado" com uma execução completa.

O `run_every` do fragmento não é simulado: o segundo `run()` faz o papel do refresh.
"""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from srag_agente import nucleo, tela
from srag_agente.config import Config

APP = str(Path(__file__).resolve().parents[2] / "src" / "srag_agente" / "app.py")


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    st.cache_resource.clear()  # cache_resource é global; session_id é fixo no AppTest
    monkeypatch.setattr(Config, "__init__", lambda self: None)
    monkeypatch.setattr(Config, "max_relatorios_sessao", 5, raising=False)
    monkeypatch.setattr(nucleo, "componentes_reais", lambda cfg: None)
    yield
    st.cache_resource.clear()


def _app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=30)


def _textos(elementos) -> list[str]:
    return [e.value for e in elementos]


def _clicar_gerar(at: AppTest) -> AppTest:
    return at.button[0].click().run()


def test_ocupado_avisa_e_mantem_o_resultado_da_propria_sessao_ate_o_novo_chegar(monkeypatch):
    anterior = tela.RelatorioTela("concluído", "sucesso", "<p>a</p>", "{}\n", 0.01)
    novo = tela.RelatorioTela("parcial", "aviso", "<p>b</p>", "{}\n", 0.02)
    vistas = []

    def gerar(*a, **k):
        # a geração está em curso (trava tomada): outra execução da tela vê "ocupado"
        vistas.append(_clicar_gerar(_app().run()))
        return novo

    monkeypatch.setattr(tela, "gerar_para_tela", lambda *a, **k: anterior)
    primeira = _app().run()
    _clicar_gerar(primeira)  # grava o resultado "anterior" no armazém da sessão
    monkeypatch.setattr(tela, "gerar_para_tela", gerar)
    _clicar_gerar(_app().run())

    (ocupada,) = vistas
    assert any("outro relatório está em geração" in t.lower() for t in _textos(ocupada.info))
    assert any("concluído" in t for t in _textos(ocupada.success))  # o dela segue na tela

    depois = _app().run()  # o fragmento reabre sem a trava: aparece o resultado novo
    assert not depois.info
    assert any("parcial" in t for t in _textos(depois.warning))
