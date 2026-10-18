"""Consulta ao dicionário (CLI e tela): validação da pergunta e rótulo da origem. Sem banco."""

import pytest

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
