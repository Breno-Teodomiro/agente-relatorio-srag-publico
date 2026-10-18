"""Erro em mensagem para quem usa o sistema (CLI e tela): sem segredo, sem dado pessoal.

O Config esconde o valor recebido, aqui só se mostra campo e mensagem, e o texto passa por
`mascarar_pii` (o psycopg repete valores de linha no DETAIL). Nunca há traceback.
"""

import psycopg
from pydantic import ValidationError

from srag_agente.agente.llm import ConfiguracaoInvalida
from srag_agente.auditoria.registro import descrever_erro
from srag_agente.guardrails.padroes import mascarar_pii
from srag_agente.metricas.repositorio import DadosIndisponiveis
from srag_agente.rag.consulta import PerguntaInvalidaError


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


def classificar(erro: Exception) -> tuple[int, str]:
    """(código de saída, mensagem): 2 = uso ou configuração, 1 = falha ao rodar."""
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
