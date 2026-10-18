"""Consulta ao dicionário (CLI e tela): validação da pergunta e rótulo da origem. Sem banco."""

from contextlib import contextmanager

import pytest

from srag_agente.config import Config
from srag_agente.ferramentas.dicionario import TrechoDicionario
from srag_agente.guardrails.entrada import LIMITE_PERGUNTA
from srag_agente.rag import consulta
from srag_agente.rag.consulta import PerguntaInvalidaError, Resposta


def _trecho(campo: str | None, distancia: float) -> TrechoDicionario:
    return TrechoDicionario(
        campo=campo, pagina=3, texto=f"Campo {campo}: texto", distancia=distancia
    )


def test_origem_distingue_nome_exato_de_similaridade():
    assert consulta.origem(_trecho("UTI", 0.0)) == "nome exato"
    assert consulta.origem(_trecho("EVOLUCAO", 0.31)) == "similaridade 0.69"


@pytest.mark.parametrize("pergunta", ["", "   \n", "x" * (LIMITE_PERGUNTA + 1)])
def test_pergunta_vazia_ou_grande_demais_e_recusada(pergunta):
    with pytest.raises(PerguntaInvalidaError):
        consulta.validar_pergunta(pergunta)


def test_pergunta_valida_volta_sem_espacos_nas_pontas():
    assert consulta.validar_pergunta("  O que é UTI?  ") == "O que é UTI?"


def test_responder_usa_a_busca_com_k_3_e_rotula_cada_trecho():
    chamadas = []

    class BuscaFalsa:
        def buscar(self, pergunta):
            chamadas.append(pergunta)
            return [_trecho("UTI", 0.0), _trecho(None, 0.5)]

    respostas = consulta.responder(BuscaFalsa(), " O que é UTI? ")
    assert chamadas == ["O que é UTI?"]
    assert respostas == [
        Resposta("UTI", 3, "Campo UTI: texto", "nome exato"),
        Resposta(None, 3, "Campo None: texto", "similaridade 0.50"),
    ]


def test_pergunta_invalida_nem_chega_a_busca():
    class BuscaFalsa:
        def buscar(self, pergunta):
            raise AssertionError("não devia buscar")

    with pytest.raises(PerguntaInvalidaError):
        consulta.responder(BuscaFalsa(), "")


def test_origem_no_modo_texto_diz_que_nao_ha_modelo():
    assert consulta.origem(_trecho("UTI", 0.0), "texto") == "nome exato"
    assert consulta.origem(_trecho("EVOLUCAO", 0.4), "texto") == "texto, sem modelo"
    # sem o 2º argumento vale o rótulo de sempre (modo vetor)
    assert consulta.origem(_trecho("EVOLUCAO", 0.4)) == "similaridade 0.60"


def test_responder_rotula_pelo_metodo_da_busca():
    class BuscaTexto:
        metodo = "texto"

        def buscar(self, pergunta):
            return [_trecho("UTI", 0.0), _trecho(None, 0.7)]

    assert [r.origem for r in consulta.responder(BuscaTexto(), "o que é UTI?")] == [
        "nome exato",
        "texto, sem modelo",
    ]


@pytest.fixture
def banco_falso(monkeypatch):
    @contextmanager
    def conectar(cfg, papel, autocommit=False):
        yield object()

    monkeypatch.setattr(consulta, "conectar", conectar)
    monkeypatch.setattr(
        consulta, "BuscaDicionario", lambda conn, embedder, k: ("busca", embedder, k)
    )


def _cfg(modo: str) -> Config:
    return Config(_env_file=None, pg_senha_admin="a", pg_senha_etl="e", pg_senha_agente="g",
                  pg_senha_auditor="u", busca_dicionario=modo)  # fmt: skip


def test_modo_texto_nao_chama_criar_embedder(banco_falso, monkeypatch):
    def proibido(pasta):
        raise AssertionError("o modo texto não pode carregar o modelo")

    monkeypatch.setattr(consulta, "criar_embedder", proibido)
    with consulta.abrir_busca(_cfg("texto")) as busca:
        assert busca == ("busca", None, consulta.K_TRECHOS)


def test_modo_texto_ignora_embedder_recebido(banco_falso, monkeypatch):
    monkeypatch.setattr(consulta, "criar_embedder", lambda pasta: pytest.fail("não carrega"))
    with consulta.abrir_busca(_cfg("texto"), embedder=lambda t: [[0.0]]) as busca:
        assert busca[1] is None


def test_modo_vetor_carrega_o_modelo_uma_vez_e_usa_o_embedder_recebido_sem_recarregar(
    banco_falso, monkeypatch
):
    carregados = []
    monkeypatch.setattr(consulta, "criar_embedder", lambda pasta: carregados.append(pasta) or "m")
    cfg = _cfg("vetor")
    with consulta.abrir_busca(cfg) as busca:
        assert busca == ("busca", "m", consulta.K_TRECHOS)
    assert carregados == [cfg.pasta_cache_modelos]
    with consulta.abrir_busca(cfg, embedder="pronto") as busca:
        assert busca[1] == "pronto"
    assert len(carregados) == 1
