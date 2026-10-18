"""CLI `srag`: argumentos, saídas e erros mostrados sem segredo. Sem banco nem LLM."""

import functools
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from srag_agente import cli
from srag_agente.agente.llm import ConfiguracaoInvalida
from srag_agente.config import RAIZ_PROJETO, Config
from srag_agente.nucleo import ResultadoExecucao
from srag_agente.relatorio.montar import ArquivosRelatorio

SENHAS = {
    "PG_SENHA_ADMIN": "adm",
    "PG_SENHA_ETL": "etl",
    "PG_SENHA_AGENTE": "agente",
    "PG_SENHA_AUDITOR": "auditor",
}


@pytest.fixture
def sem_env(monkeypatch):
    """A CLI lê o Config sem o .env real da raiz (que tem as senhas de verdade)."""
    for campo in Config.model_fields:
        monkeypatch.delenv(campo.upper(), raising=False)
    monkeypatch.setattr(cli, "Config", functools.partial(Config, _env_file=None))


@pytest.fixture
def com_senhas(sem_env, monkeypatch):
    for chave, valor in SENHAS.items():
        monkeypatch.setenv(chave, valor)


# --- grafo ------------------------------------------------------------------------------------
def test_grafo_gera_o_mermaid_a_partir_do_codigo(tmp_path):
    destino = tmp_path / "docs" / "grafo.mmd"
    assert cli.main(["grafo", "--saida", str(destino)]) == 0
    conteudo = destino.read_text(encoding="utf-8")
    for no in ("guardrail_entrada", "orquestrador", "ferramentas", "verificar_cobertura",
               "redator", "guardrail_saida"):  # fmt: skip
        assert no in conteudo
    # a aresta condicional da Task 13: teto atingido nas ferramentas vai direto à cobertura
    assert "ferramentas -.-> verificar_cobertura" in conteudo


def test_grafo_e_qualidade_vao_para_docs_da_raiz_por_padrao():
    assert cli.ARQUIVO_GRAFO == RAIZ_PROJETO / "docs" / "grafo_langgraph.mmd"
    assert cli.ARQUIVO_QUALIDADE == RAIZ_PROJETO / "docs" / "QUALIDADE_DADOS.md"


def test_grafo_nao_precisa_de_configuracao(sem_env, tmp_path):
    # sem nenhuma senha no ambiente: o grafo sai mesmo assim
    assert cli.main(["grafo", "--saida", str(tmp_path / "g.mmd")]) == 0


# --- argumentos -------------------------------------------------------------------------------
def test_uf_invalida_e_recusada_antes_de_tudo():
    with pytest.raises(SystemExit) as saida:
        cli.main(["relatorio", "--uf", "ZZ"])
    assert saida.value.code == 2


def test_uf_em_minusculas_e_aceita():
    assert cli._argumentos(["relatorio", "--uf", "sp"]).uf == "SP"


def test_sem_subcomando_e_erro_de_uso():
    with pytest.raises(SystemExit) as saida:
        cli.main([])
    assert saida.value.code == 2


# --- relatorio --------------------------------------------------------------------------------
def _resultado(tmp_path: Path, status: str = "ok") -> ResultadoExecucao:
    arquivos = ArquivosRelatorio(
        pasta=tmp_path,
        html=tmp_path / "relatorio.html",
        markdown=tmp_path / "relatorio.md",
        auditoria=tmp_path / "auditoria.jsonl",
    )
    return ResultadoExecucao(id_execucao=uuid4(), status=status, arquivos=arquivos,
                             tokens_entrada=10, tokens_saida=5, custo_usd=0.0123)  # fmt: skip


def test_relatorio_chama_o_nucleo_e_mostra_os_arquivos(com_senhas, tmp_path, monkeypatch, capsys):
    chamadas = []

    def falso(cfg, *, uf, pergunta_extra):
        chamadas.append((uf, pergunta_extra))
        return _resultado(tmp_path)

    monkeypatch.setattr("srag_agente.nucleo.gerar_relatorio", falso)
    assert cli.main(["relatorio", "--uf", "rj", "--pergunta", "Como está a UTI?"]) == 0
    assert chamadas == [("RJ", "Como está a UTI?")]
    saida = capsys.readouterr().out
    assert "Situação: ok" in saida and "relatorio.html" in saida and "US$ 0.0123" in saida


def test_relatorio_sem_chave_de_llm_explica_e_sai_com_2(com_senhas, monkeypatch, capsys):
    def sem_chave(cfg, **_):
        raise ConfiguracaoInvalida("Defina OPENROUTER_API_KEY ou OPENAI_API_KEY no .env.")

    monkeypatch.setattr("srag_agente.nucleo.gerar_relatorio", sem_chave)
    assert cli.main(["relatorio"]) == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


def test_banco_fora_do_ar_explica_e_sai_com_1(com_senhas, monkeypatch, capsys):
    def banco_fora(cfg, **_):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr("srag_agente.nucleo.gerar_relatorio", banco_fora)
    assert cli.main(["relatorio"]) == 1
    erro = capsys.readouterr().err
    assert "make subir" in erro and "connection refused" in erro


# --- configuração -----------------------------------------------------------------------------
def test_configuracao_incompleta_nao_mostra_segredo(sem_env, monkeypatch, capsys):
    monkeypatch.setenv("PG_SENHA_ADMIN", "segredo-que-nao-pode-vazar")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-chave-que-nao-pode-vazar")
    assert cli.main(["relatorio"]) == 2
    saida = capsys.readouterr()
    texto = saida.out + saida.err
    assert "pg_senha_etl" in texto and ".env" in texto
    assert "segre" not in texto and "sk-chave" not in texto


# --- carga, indexar, qualidade ----------------------------------------------------------------
def test_carga_roda_raw_e_transformacao_juntas(com_senhas, monkeypatch, capsys):
    # A CLI chama a carga atômica (raw + silver + gold numa transação), nunca só a raw.
    chamadas = []
    monkeypatch.setattr("srag_agente.dados.carga.executar_carga",
                        lambda cfg: chamadas.append(cfg) or 165397)  # fmt: skip
    assert cli.main(["carga"]) == 0
    assert len(chamadas) == 1
    assert "165.397 linhas na raw, silver e gold refeitas" in capsys.readouterr().out


def test_qualidade_grava_onde_pedido(com_senhas, monkeypatch, tmp_path):
    destinos = []

    class ConexaoFalsa:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def gerar(conn, destino):
        destinos.append(destino)
        return destino

    monkeypatch.setattr("srag_agente.banco.conectar", lambda cfg, papel: ConexaoFalsa())
    monkeypatch.setattr("srag_agente.dados.qualidade.gerar_relatorio_qualidade", gerar)
    assert cli.main(["qualidade", "--saida", str(tmp_path / "q.md")]) == 0
    assert destinos == [tmp_path / "q.md"]
