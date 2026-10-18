"""Erro em mensagem para quem usa o sistema (CLI e tela): sem segredo, sem dado pessoal.

O Config esconde o valor recebido, aqui só se mostra campo e mensagem, e o texto passa por
`mascarar_pii` (o psycopg repete valores de linha no DETAIL). Nunca há traceback.

Na demonstração pública (`publico=True`) o visitante não vê detalhe nenhum (o erro de conexão do
psycopg traz host, porta e usuário): recebe uma mensagem genérica com uma referência curta, e o
log do servidor registra só a referência, o tipo do erro e o SQLSTATE, nunca o texto.
"""

import logging
import re
import secrets

import psycopg
from pydantic import ValidationError

from srag_agente.agente.llm import ConfiguracaoInvalida
from srag_agente.auditoria.registro import descrever_erro
from srag_agente.demo import SQLSTATE_TETO
from srag_agente.demo.teto import MENSAGEM_TETO_BANCO
from srag_agente.guardrails.padroes import mascarar_pii
from srag_agente.metricas.repositorio import DadosIndisponiveis
from srag_agente.rag.consulta import PerguntaInvalidaError

log = logging.getLogger(__name__)

MENSAGEM_BANCO_DEMO = (
    "O banco da demonstração não respondeu agora (ele hiberna depois de um tempo sem uso e leva "
    "alguns segundos para voltar). Tente de novo em instantes."
)
MENSAGEM_FALHA_DEMO = "Não foi possível concluir agora; tente de novo mais tarde."


def texto_seguro(erro: BaseException, *, com_tipo: bool = True) -> str:
    """Erro pronto para exibir: sem o `input` do pydantic e com dado pessoal mascarado."""
    texto = descrever_erro(erro) if com_tipo else str(erro)
    return mascarar_pii(texto)[0]


def mensagem_configuracao(erro: ValidationError) -> str:
    campos = "\n".join(
        f"  - {'.'.join(map(str, e['loc']))}: {e['msg']}"
        for e in erro.errors(include_url=False, include_input=False, include_context=False)
    )
    return f"Configuração inválida (confira o .env; modelo em .env.example):\n{campos}"


def _recusa_do_teto(erro: Exception) -> bool:
    return isinstance(erro, psycopg.Error) and getattr(erro, "sqlstate", None) == SQLSTATE_TETO


def registrar_falha(erro: BaseException) -> str:
    """Registra no log só a referência, o tipo e o SQLSTATE; devolve a referência.

    O texto do erro fica de fora: pode ter host, usuário ou segredo.
    """
    referencia = secrets.token_hex(4)
    sqlstate = getattr(erro, "sqlstate", None) if isinstance(erro, psycopg.Error) else None
    log.warning(
        "falha na demonstração (referência %s): %s sqlstate=%s",
        referencia,
        type(erro).__name__,
        re.sub(r"[^0-9A-Za-z]", "", sqlstate or "") or "-",
    )
    return referencia


def _classificar_publico(erro: Exception) -> tuple[int, str]:
    if isinstance(erro, PerguntaInvalidaError):  # texto nosso, sobre o que o visitante digitou
        return 2, str(erro)
    referencia = registrar_falha(erro)
    codigo = 2 if isinstance(erro, ConfiguracaoInvalida) else 1
    base = (
        MENSAGEM_BANCO_DEMO if isinstance(erro, psycopg.OperationalError) else MENSAGEM_FALHA_DEMO
    )
    return codigo, f"{base} Referência: {referencia}."


def classificar(erro: Exception, *, publico: bool = False) -> tuple[int, str]:
    """(código de saída, mensagem): 2 = uso ou configuração, 1 = falha ao rodar.

    `publico=True` é a tela da demonstração: mensagem genérica, sem infraestrutura.
    """
    if _recusa_do_teto(erro):  # gatilho do teto diário (corrida entre processos)
        return 1, MENSAGEM_TETO_BANCO
    if publico:
        return _classificar_publico(erro)
    if isinstance(erro, PerguntaInvalidaError):
        return 2, str(erro)
    if isinstance(erro, ConfiguracaoInvalida):
        return 2, f"Configuração incompleta: {texto_seguro(erro, com_tipo=False)}"
    if isinstance(erro, psycopg.OperationalError):
        # a mensagem do psycopg traz host, porta e usuário; a senha nunca
        return 1, (
            "Não foi possível falar com o Postgres (o banco está de pé? `make subir`): "
            f"{texto_seguro(erro, com_tipo=False)}"
        )
    if isinstance(erro, psycopg.Error):
        return 1, (
            "Erro no Postgres. Se o banco é novo ou foi recriado, rode `make carga` e depois "
            f"`make indexar`. Detalhe: {texto_seguro(erro)}"
        )
    if isinstance(erro, DadosIndisponiveis):
        return 1, f"Sem dados na base ({texto_seguro(erro, com_tipo=False)}). Rode `make carga`."
    if isinstance(erro, FileNotFoundError):
        return 1, (
            f"Arquivo não encontrado: {mascarar_pii(str(erro.filename))[0]} "
            "(confira ARQUIVO_DADOS e ARQUIVO_DICIONARIO no .env)."
        )
    return 1, f"Falha inesperada: {texto_seguro(erro)}"
