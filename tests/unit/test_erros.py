"""Tradução de erro em mensagem para quem usa (CLI e tela): sem segredo, sem dado pessoal."""

import psycopg
import pytest

from srag_agente.agente.llm import ConfiguracaoInvalida
from srag_agente.demo import SQLSTATE_TETO
from srag_agente.demo.teto import MENSAGEM_TETO_BANCO
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


# --- demonstração online: recusa do gatilho do teto e mensagem pública ---

HOST = "ep-canario-123.sa-east-1.aws.neon.tech"
SENHA = "canario-senha-9f8e7d"  # fictícia: só marca para procurar no texto


def _erro_do_gatilho() -> psycopg.Error:
    # o psycopg não tem classe para a classe `SR`: chega como DatabaseError com o sqlstate
    erro = psycopg.DatabaseError("limite diário de 10 relatórios da demonstração atingido")
    erro.sqlstate = SQLSTATE_TETO
    return erro


def _erro_de_conexao() -> psycopg.OperationalError:
    return psycopg.OperationalError(
        f'connection to server at "{HOST}" (10.0.0.1), port 5432 failed: FATAL: password '
        f'authentication failed for user "srag_agente" password={SENHA}'
    )


@pytest.mark.parametrize("publico", [False, True])
def test_recusa_do_gatilho_vira_a_mensagem_do_teto(publico):
    codigo, mensagem = classificar(_erro_do_gatilho(), publico=publico)
    assert (codigo, mensagem) == (1, MENSAGEM_TETO_BANCO)
    assert "relatórios da demonstração atingido" not in mensagem  # texto do Postgres não sai


def test_sqlstate_diferente_nao_vira_teto():
    erro = psycopg.DatabaseError("outro")
    erro.sqlstate = "SR430"
    assert classificar(erro)[1] != MENSAGEM_TETO_BANCO


def test_local_continua_mostrando_o_detalhe_da_conexao():
    _, mensagem = classificar(_erro_de_conexao())
    assert "make subir" in mensagem and HOST in mensagem  # comportamento local de sempre


@pytest.mark.parametrize(
    "erro",
    [
        _erro_de_conexao(),
        psycopg.errors.UndefinedTable(f"relation auditoria.limites_demo does not exist {HOST}"),
        ConfiguracaoInvalida(f"PG_SENHA_ADMIN não definida {SENHA}"),
        RuntimeError(f"falhou em {HOST} com {SENHA} e {CPF}"),
        DadosIndisponiveis(f"base vazia {HOST}"),
        FileNotFoundError(2, "No such file", f"/app/{SENHA}.zip"),
    ],
)
def test_publico_nao_mostra_nem_registra_infraestrutura(erro, caplog):
    caplog.set_level("DEBUG")
    codigo, mensagem = classificar(erro, publico=True)
    assert codigo in (1, 2)
    assert "Referência:" in mensagem
    referencia = mensagem.rsplit("Referência:", 1)[1].strip(" .")
    assert referencia in caplog.text  # o dono acha no log o tipo do erro pela referência
    assert type(erro).__name__ in caplog.text
    # a referência é aleatória (hex): sai da comparação para não casar por acaso com "456"
    tela, registrado = mensagem.replace(referencia, ""), caplog.text.replace(referencia, "")
    for proibido in (HOST, "10.0.0.1", "5432", '"srag_agente"', SENHA, "456", "make "):
        assert proibido not in tela
        assert proibido not in registrado


def test_publico_mantem_a_mensagem_da_pergunta_invalida():
    assert classificar(PerguntaInvalidaError("Escreva uma pergunta"), publico=True) == (
        2, "Escreva uma pergunta"
    )  # fmt: skip


def test_publico_conexao_explica_a_hibernacao_sem_detalhe():
    _, mensagem = classificar(_erro_de_conexao(), publico=True)
    assert "banco da demonstração" in mensagem


def test_cada_falha_publica_ganha_referencia_propria():
    a = classificar(RuntimeError("x"), publico=True)[1]
    b = classificar(RuntimeError("x"), publico=True)[1]
    assert a != b
