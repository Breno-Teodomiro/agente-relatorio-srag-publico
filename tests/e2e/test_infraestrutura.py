"""E2E 5 — falha de infraestrutura: disco sem permissão, banco fora do ar, LLM que não responde.

Duas camadas: o núcleo (`gerar_relatorio`), que encerra a execução como `erro` e deixa a trilha no
banco, e a CLI (`srag relatorio`), que traduz o erro em mensagem curta, código de saída e nenhum
traceback cru.
"""

import os
import socket
import stat

import psycopg
import pytest
from langchain_core.runnables import RunnableLambda

from srag_agente import cli
from tests.e2e.apoio import Simulacao, execucao_depois_de, ultima_execucao
from tests.fakes import texto_neutro

pytestmark = pytest.mark.integracao

SENHA_FALSA = "senha-que-nunca-pode-aparecer"


@pytest.fixture
def cli_com_falsos(monkeypatch, cfg_teste, pasta_saida):
    """A CLI de verdade (Config pelo ambiente), com o LLM e a busca trocados por dublês."""
    ambiente = {
        "PG_HOST": cfg_teste.pg_host, "PG_PORTA": str(cfg_teste.pg_porta),
        "PG_BANCO": cfg_teste.pg_banco, "PASTA_RELATORIOS": str(pasta_saida),
        "PG_SENHA_ADMIN": cfg_teste.pg_senha_admin.get_secret_value(),
        "PG_SENHA_ETL": cfg_teste.pg_senha_etl.get_secret_value(),
        "PG_SENHA_AGENTE": cfg_teste.pg_senha_agente.get_secret_value(),
        "PG_SENHA_AUDITOR": cfg_teste.pg_senha_auditor.get_secret_value(),
    }  # fmt: skip
    for chave, valor in ambiente.items():
        monkeypatch.setenv(chave, valor)

    def preparar(simulacao: Simulacao | None = None):
        simulacao = simulacao or Simulacao(textos=[texto_neutro()])
        monkeypatch.setattr("srag_agente.nucleo.componentes_reais",
                            lambda _cfg: simulacao.componentes())  # fmt: skip
        return simulacao

    return preparar


def _porta_sem_ninguem() -> int:
    """Porta que acabou de ser liberada: a conexão é recusada na hora (a porta 1 trava no WSL)."""
    with socket.socket() as soquete:
        soquete.bind(("127.0.0.1", 0))
        return soquete.getsockname()[1]


def _sem_traceback(saida: str) -> None:
    assert "Traceback" not in saida and 'File "' not in saida


def _pasta_sem_permissao(tmp_path):
    pasta = tmp_path / "somente_leitura"
    pasta.mkdir()
    pasta.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return pasta


pytestmark_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignora permissão de pasta"
)


@pytestmark_root
def test_pasta_sem_permissao_encerra_como_erro_e_nao_deixa_trilha(
    cfg_teste, banco_carregado, conn_etl, tmp_path
):
    pasta = _pasta_sem_permissao(tmp_path)
    antes = ultima_execucao(conn_etl)
    try:
        with pytest.raises(PermissionError):
            Simulacao(textos=[texto_neutro()]).rodar(cfg_teste, pasta)
    finally:
        pasta.chmod(stat.S_IRWXU)
    execucao = execucao_depois_de(conn_etl, antes)
    assert execucao["status"] == "erro" and execucao["status"] != "ok"
    assert execucao["fim"] is not None
    assert any("PermissionError" in e for e in execucao["erros"])
    assert list(pasta.rglob("*")) == []  # sem relatório, sem gráficos, sem auditoria.jsonl


def test_pasta_que_e_um_arquivo_encerra_como_erro(cfg_teste, banco_carregado, conn_etl, tmp_path):
    """Vale também para root: o disco não tem onde gravar, a execução não pode ficar `ok`."""
    arquivo = tmp_path / "nao_e_pasta"
    arquivo.write_text("ocupado")
    antes = ultima_execucao(conn_etl)
    with pytest.raises(OSError):  # NotADirectoryError ou FileExistsError
        Simulacao(textos=[texto_neutro()]).rodar(cfg_teste, arquivo)
    execucao = execucao_depois_de(conn_etl, antes)
    assert execucao["status"] == "erro" and execucao["fim"] is not None
    assert execucao["tokens_entrada"] > 0  # o gasto do agente entra no encerramento de erro
    assert arquivo.read_text() == "ocupado"


@pytestmark_root
def test_cli_com_pasta_sem_permissao_sai_com_1_sem_traceback(
    cli_com_falsos, monkeypatch, tmp_path, cfg_teste, banco_carregado, conn_etl, capsys
):
    pasta = _pasta_sem_permissao(tmp_path)
    monkeypatch.setenv("PASTA_RELATORIOS", str(pasta))
    cli_com_falsos()
    antes = ultima_execucao(conn_etl)
    try:
        codigo = cli.main(["relatorio"])
    finally:
        pasta.chmod(stat.S_IRWXU)
    saida = capsys.readouterr()
    assert codigo == 1
    assert "Falha inesperada: PermissionError" in saida.err
    _sem_traceback(saida.out + saida.err)
    assert execucao_depois_de(conn_etl, antes)["status"] == "erro"
    assert list(pasta.rglob("*")) == []


def test_cli_com_banco_fora_do_ar_explica_e_sai_com_1_sem_traceback(
    cli_com_falsos, monkeypatch, pasta_saida, capsys
):
    monkeypatch.setenv("PG_HOST", "127.0.0.1")
    monkeypatch.setenv("PG_PORTA", str(_porta_sem_ninguem()))  # nada escuta lá
    monkeypatch.setenv("PG_SENHA_AGENTE", SENHA_FALSA)
    cli_com_falsos()
    codigo = cli.main(["relatorio", "--uf", "SP"])
    saida = capsys.readouterr()
    assert codigo == 1
    assert "Não foi possível falar com o Postgres" in saida.err and "make subir" in saida.err
    _sem_traceback(saida.out + saida.err)
    assert SENHA_FALSA not in saida.out + saida.err
    assert not pasta_saida.exists()  # nada foi gerado


def test_nucleo_com_banco_fora_do_ar_levanta_erro_do_banco_e_nao_gera_arquivo(cfg_teste, tmp_path):
    cfg = cfg_teste.model_copy(update={"pg_host": "127.0.0.1", "pg_porta": _porta_sem_ninguem()})
    with pytest.raises(psycopg.OperationalError):
        Simulacao(textos=[texto_neutro()]).rodar(cfg, tmp_path / "relatorios")
    assert not (tmp_path / "relatorios").exists()


def test_llm_que_nao_responde_encerra_como_erro_e_a_cli_explica(
    cli_com_falsos, cfg_teste, banco_carregado, conn_etl, pasta_saida, capsys
):
    def fora_do_ar(_mensagens):
        raise TimeoutError("provedor de LLM não respondeu")

    cli_com_falsos(Simulacao(orquestrador=RunnableLambda(fora_do_ar)))
    antes = ultima_execucao(conn_etl)
    codigo = cli.main(["relatorio"])
    saida = capsys.readouterr()
    assert codigo == 1
    assert "Falha inesperada: TimeoutError" in saida.err
    _sem_traceback(saida.out + saida.err)
    execucao = execucao_depois_de(conn_etl, antes)
    assert execucao["status"] == "erro" and execucao["fim"] is not None
    assert not list(pasta_saida.rglob("auditoria.jsonl"))
