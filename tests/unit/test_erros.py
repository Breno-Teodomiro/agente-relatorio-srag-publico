"""Tradução de erro em mensagem para quem usa (CLI e tela): sem segredo, sem dado pessoal."""

import psycopg
import pytest

from srag_agente.agente.llm import ConfiguracaoInvalida
from srag_agente.erros import classificar
from srag_agente.metricas.repositorio import DadosIndisponiveis
from srag_agente.rag.consulta import PerguntaInvalidaError

CPF = "123.456.789-09"  # fictício


@pytest.mark.parametrize(
    "erro,codigo,dica",
    [
        (ConfiguracaoInvalida("Defina OPENROUTER_API_KEY"), 2, "OPENROUTER_API_KEY"),
        (PerguntaInvalidaError("Escreva uma pergunta"), 2, "Escreva uma pergunta"),
        (psycopg.OperationalError(f"connection refused {CPF}"), 1, "make subir"),
        (psycopg.errors.UndefinedTable("relation x does not exist"), 1, "make carga"),
        (DadosIndisponiveis("base vazia"), 1, "make carga"),
        (FileNotFoundError(2, "No such file", "/x/INFLUD.zip"), 1, "ARQUIVO_DADOS"),
        (RuntimeError(f"falhou para {CPF}"), 1, "RuntimeError"),
    ],
)
def test_classifica_codigo_e_mensagem_mascarada(erro, codigo, dica):
    resultado, mensagem = classificar(erro)
    assert resultado == codigo
    assert dica in mensagem
    assert "456" not in mensagem and "Traceback" not in mensagem
