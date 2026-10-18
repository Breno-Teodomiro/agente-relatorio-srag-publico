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


# --- M3: falha ao rodar sai limpa, sem texto de erro cru no terminal --------------------------
CPF = "123.456.789-09"  # fictício


def _modelo_invalido():
    from pydantic import BaseModel, ValidationError

    class Modelo(BaseModel):
        quantidade: int

    try:
        Modelo(quantidade=f"valor cru {CPF}")
    except ValidationError as erro:
        return erro


def _quebrar(monkeypatch, erro):
    def quebra(cfg, **_):
        raise erro

    monkeypatch.setattr("srag_agente.nucleo.gerar_relatorio", quebra)


@pytest.mark.parametrize(
    "erro,codigo,dica",
    [
        (psycopg.errors.UndefinedTable(f"relation gold.x does not exist {CPF}"), 1, "make carga"),
        (psycopg.errors.UndefinedTable("relation rag.trechos does not exist"), 1, "make indexar"),
        (psycopg.OperationalError(f"connection refused {CPF}"), 1, "make subir"),
        (RuntimeError(f"falhou para {CPF}"), 1, "RuntimeError"),
        (_modelo_invalido(), 1, "quantidade"),
        (KeyboardInterrupt(), 130, "nterrompid"),
    ],
    ids=["psycopg", "psycopg_indexar", "operacional", "inesperado", "validacao", "ctrl_c"],
)
def test_falha_ao_rodar_sai_limpa_e_mascarada(com_senhas, monkeypatch, capsys, erro, codigo, dica):
    _quebrar(monkeypatch, erro)
    assert cli.main(["relatorio"]) == codigo
    saida = capsys.readouterr()
    texto = saida.out + saida.err
    assert dica in texto
    assert "456" not in texto and "valor cru" not in texto  # nem o CPF nem o valor recebido
    assert "Traceback" not in texto


def test_dados_indisponiveis_explicam_a_carga(com_senhas, monkeypatch, capsys):
    from srag_agente.metricas.repositorio import DadosIndisponiveis

    _quebrar(monkeypatch, DadosIndisponiveis("base vazia"))
    assert cli.main(["relatorio"]) == 1
    erro = capsys.readouterr().err
    assert "base vazia" in erro and "make carga" in erro


def test_zip_ausente_na_carga_explica_onde_procurou(com_senhas, monkeypatch, capsys):
    destino = "/nao/existe/INFLUD25.zip"

    def sem_zip(cfg):
        raise FileNotFoundError(2, "No such file or directory", destino)

    monkeypatch.setattr("srag_agente.dados.carga.executar_carga", sem_zip)
    assert cli.main(["carga"]) == 1
    erro = capsys.readouterr().err
    assert destino in erro and "ARQUIVO_DADOS" in erro and "Errno" not in erro


# --- configuração -----------------------------------------------------------------------------
def test_configuracao_incompleta_nao_mostra_segredo(sem_env, monkeypatch, capsys):
    monkeypatch.setenv("PG_SENHA_ADMIN", "segredo-que-nao-pode-vazar")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-chave-que-nao-pode-vazar")
    assert cli.main(["relatorio"]) == 2
    saida = capsys.readouterr()
    texto = saida.out + saida.err
    assert "pg_senha_etl" in texto and ".env" in texto
    assert "segre" not in texto and "sk-chave" not in texto


@pytest.mark.parametrize(
    "variavel,valor",
    [("MAX_SEGUNDOS_EXECUCAO", "nan"), ("MAX_TOKENS_EXECUCAO", "-1"),
     ("MAX_CHAMADAS_FERRAMENTA", "0"), ("PRECO_SAIDA_USD_MTOK", "-1.6")],
)  # fmt: skip
def test_teto_ou_preco_invalido_no_ambiente_sai_com_2(com_senhas, monkeypatch, capsys,
                                                      variavel, valor):  # fmt: skip
    chamado = []
    monkeypatch.setattr("srag_agente.nucleo.gerar_relatorio", lambda *a, **k: chamado.append(1))
    monkeypatch.setenv(variavel, valor)
    assert cli.main(["relatorio"]) == 2
    assert variavel.lower() in capsys.readouterr().err
    assert chamado == []  # nem chega a rodar o agente


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


# --- dicionario -------------------------------------------------------------------------------
def test_dicionario_mostra_campo_texto_e_origem(com_senhas, monkeypatch, capsys):
    from contextlib import nullcontext

    from srag_agente.rag.consulta import Resposta

    monkeypatch.setattr("srag_agente.rag.consulta.abrir_busca", lambda cfg: nullcontext("busca"))
    monkeypatch.setattr(
        "srag_agente.rag.consulta.responder",
        lambda busca, pergunta: [Resposta("UTI", 7, "Campo UTI: internado em UTI", "nome exato")],
    )
    assert cli.main(["dicionario", "o que é UTI?"]) == 0
    saida = capsys.readouterr().out
    assert "[UTI] p.7 (nome exato)" in saida and "internado em UTI" in saida


def test_dicionario_pergunta_vazia_sai_com_2_e_explica(com_senhas, monkeypatch, capsys):
    from contextlib import nullcontext

    monkeypatch.setattr("srag_agente.rag.consulta.abrir_busca", lambda cfg: nullcontext("busca"))
    assert cli.main(["dicionario", "  "]) == 2
    assert "Escreva uma pergunta" in capsys.readouterr().err


# --- demo: exportar | carregar | conferir -----------------------------------------------------
HOST_DEMO = "ep-exemplo-123.sa-east-1.aws.exemplo.tech"
CANARIO = "canario-Xy7-senha"


@pytest.fixture
def env_demo(sem_env, tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("52.67.10.20", 0))])  # fmt: skip
    arquivo = tmp_path / ".env.demo"
    arquivo.write_text(
        f"PG_HOST={HOST_DEMO}\nPG_PORTA=5432\nPG_BANCO=demo\nPG_USUARIO_ADMIN=dono_canario\n"
        f"PG_SSLMODE=verify-full\nPG_SENHA_ADMIN={CANARIO}-admin\n"
        f"PG_SENHA_AGENTE={CANARIO}-agente\nPG_SENHA_AUDITOR={CANARIO}-auditor\n",
        encoding="utf-8",
    )
    return arquivo


def _resumo(ok: bool = True):
    from datetime import date

    from srag_agente.demo.carregar import ResumoCarga
    from srag_agente.demo.conferir import Conferencia

    return ResumoCarga(
        sha256="a" * 64, data_referencia=date(2025, 6, 25), linhas={"referencia": 1},
        papeis_criados=("srag_agente",), senhas_enviadas=("srag_agente",),
        conferencias=(Conferencia("agente", "SHOW statement_timeout", "5s", ok, "5s"),),
        avisos=(),
    )  # fmt: skip


@pytest.mark.parametrize(
    "argv",
    [["demo"], ["demo", "carregar", "--alvo", "x"], ["demo", "carregar", "--env-arquivo", "x"],
     ["demo", "conferir", "--alvo", "x"], ["demo", "conferir", "--env-arquivo", "x"],
     ["demo", "carregar", "--env-arquivo", "x", "--alvo", "x", "--permitir-local"]],
    ids=["sem_acao", "carregar_sem_env", "carregar_sem_alvo", "conferir_sem_env",
         "conferir_sem_alvo", "sem_opcao_permitir_local"],
)  # fmt: skip
def test_demo_exige_env_arquivo_e_alvo(argv):
    with pytest.raises(SystemExit) as saida:
        cli.main(argv)
    assert saida.value.code == 2


def test_demo_argumentos_de_carregar():
    args = cli._argumentos(["demo", "carregar", "--env-arquivo", ".env.demo", "--alvo", "h",
                            "--senha-em-texto", "--trocar-senhas"])  # fmt: skip
    assert (args.comando, args.acao, args.alvo) == ("demo", "carregar", "h")
    assert args.env_arquivo == Path(".env.demo") and args.senha_em_texto and args.trocar_senhas
    assert args.pacote == cli.PASTA_PACOTE == RAIZ_PROJETO / "demo" / "pacote"
    padrao = cli._argumentos(["demo", "carregar", "--env-arquivo", "e", "--alvo", "h"])
    assert padrao.senha_em_texto and not padrao.trocar_senhas  # texto sobre TLS é o padrão
    em_hash = cli._argumentos(["demo", "carregar", "--env-arquivo", "e", "--alvo", "h",
                               "--senha-em-hash"])  # fmt: skip
    assert em_hash.senha_em_texto is False
    with pytest.raises(SystemExit):
        cli._argumentos(["demo", "carregar", "--env-arquivo", "e", "--alvo", "h",
                         "--senha-em-texto", "--senha-em-hash"])  # fmt: skip


def test_demo_carregar_recusa_o_env_local(sem_env, monkeypatch, capsys):
    monkeypatch.setattr("srag_agente.demo.carregar.carregar",
                        lambda *a, **k: pytest.fail("não podia carregar"))  # fmt: skip
    codigo = cli.main(["demo", "carregar", "--env-arquivo", str(RAIZ_PROJETO / ".env"),
                       "--alvo", "localhost"])  # fmt: skip
    assert codigo == 2
    assert ".env.demo" in capsys.readouterr().err


def test_demo_carregar_alvo_diferente_sai_com_2_sem_carregar(env_demo, monkeypatch, capsys):
    from srag_agente.demo import carregar

    chamado = []
    monkeypatch.setattr(carregar, "ler_pacote", lambda pasta: chamado.append(pasta))
    codigo = cli.main(["demo", "carregar", "--env-arquivo", str(env_demo),
                       "--alvo", "outro.exemplo.tech"])  # fmt: skip
    assert codigo == 2 and chamado == []
    saida = capsys.readouterr()
    texto = saida.out + saida.err
    assert "--alvo" in texto and CANARIO not in texto and HOST_DEMO not in texto


def test_demo_carregar_le_o_arquivo_e_passa_as_opcoes(env_demo, monkeypatch, capsys):
    chamadas = []

    def falso(cfg, pasta, **opcoes):
        chamadas.append((cfg, pasta, opcoes))
        return _resumo()

    monkeypatch.setattr("srag_agente.demo.carregar.carregar", falso)
    monkeypatch.setenv("PG_HOST", "localhost")  # exportada no shell: não vale para a carga
    codigo = cli.main(["demo", "carregar", "--env-arquivo", str(env_demo), "--alvo", HOST_DEMO,
                       "--senha-em-hash", "--pacote", "/tmp/p"])  # fmt: skip
    assert codigo == 0
    cfg, pasta, opcoes = chamadas[0]
    assert cfg.pg_host == HOST_DEMO and cfg.pg_usuario_admin == "dono_canario"
    assert pasta == Path("/tmp/p")
    assert opcoes == {
        "alvo": HOST_DEMO,
        "senha_em_texto": False,
        "trocar_senhas": False,
        "aceitar_tls_sem_verificacao": False,
    }
    saida = capsys.readouterr()
    assert "a" * 64 in saida.out and "2025-06-25" in saida.out
    assert CANARIO not in saida.out + saida.err


def test_demo_carregar_com_conferencia_falha_sai_com_1(env_demo, monkeypatch, capsys):
    monkeypatch.setattr("srag_agente.demo.carregar.carregar", lambda *a, **k: _resumo(ok=False))
    assert cli.main(["demo", "carregar", "--env-arquivo", str(env_demo), "--alvo", HOST_DEMO]) == 1
    assert "FALHOU" in capsys.readouterr().out


@pytest.mark.parametrize(
    "erro,dica",
    [
        (psycopg.OperationalError(
            f'connection to server at "{HOST_DEMO}" (1.2.3.4), port 5432 failed: FATAL:  '
            'password authentication failed for user "dono_canario"'), "senha"),
        (psycopg.OperationalError(f'could not translate host name "{HOST_DEMO}"'), "host"),
        (psycopg.OperationalError(f"server {HOST_DEMO}: certificate verify failed"), "TLS"),
        (psycopg.errors.SyntaxError(f"syntax error at or near \"{CANARIO}-admin\""), "Postgres"),
        (RuntimeError(f"falhou com {CANARIO}-agente em {HOST_DEMO}"), "RuntimeError"),
    ],
    ids=["senha", "host", "tls", "sintaxe_com_senha", "inesperado"],
)  # fmt: skip
def test_demo_erro_nao_mostra_senha_host_nem_usuario(env_demo, monkeypatch, capsys, erro, dica):
    def quebra(*_, **__):
        raise erro

    monkeypatch.setattr("srag_agente.demo.carregar.carregar", quebra)
    codigo = cli.main(["demo", "carregar", "--env-arquivo", str(env_demo), "--alvo", HOST_DEMO])
    assert codigo == 1
    saida = capsys.readouterr()
    texto = saida.out + saida.err
    assert dica in texto
    assert CANARIO not in texto and "dono_canario" not in texto and "1.2.3.4" not in texto
    assert HOST_DEMO not in texto and "Traceback" not in texto


def test_demo_erro_com_senha_no_texto_e_apagado(env_demo, monkeypatch, capsys):
    # Recusa que (por engano futuro) repetisse a senha: a CLI apaga antes de mostrar.
    from srag_agente.demo.carregar import RecusaCarga

    def quebra(*_, **__):
        raise RecusaCarga(f"recusado {CANARIO}-admin")

    monkeypatch.setattr("srag_agente.demo.carregar.carregar", quebra)
    assert cli.main(["demo", "carregar", "--env-arquivo", str(env_demo), "--alvo", HOST_DEMO]) == 2
    saida = capsys.readouterr()
    assert "recusado" in saida.err and CANARIO not in saida.out + saida.err


def test_demo_arquivo_sem_senha_do_agente_sai_com_2(env_demo, capsys):
    texto = env_demo.read_text(encoding="utf-8").replace(f"PG_SENHA_AGENTE={CANARIO}-agente\n", "")
    env_demo.write_text(texto, encoding="utf-8")
    assert cli.main(["demo", "conferir", "--env-arquivo", str(env_demo), "--alvo", HOST_DEMO]) == 2
    erro = capsys.readouterr().err
    assert "pg_senha_agente" in erro and CANARIO not in erro


def test_demo_conferir_mostra_cada_tentativa(env_demo, monkeypatch, capsys):
    from srag_agente.demo.conferir import Conferencia

    lista = [Conferencia("agente", "SHOW statement_timeout", "5s", True, "5s"),
             Conferencia("auditor", "DELETE", "InsufficientPrivilege", True,
                         "InsufficientPrivilege")]  # fmt: skip
    monkeypatch.setattr("srag_agente.demo.conferir.conferir_demo", lambda cfg: lista)
    assert cli.main(["demo", "conferir", "--env-arquivo", str(env_demo), "--alvo", HOST_DEMO]) == 0
    saida = capsys.readouterr().out
    assert "SHOW statement_timeout" in saida and "2 de 2" in saida
    lista.append(Conferencia("catalogo", "esquemas raw/silver", "vazio", False, "1 objeto(s)"))
    assert cli.main(["demo", "conferir", "--env-arquivo", str(env_demo), "--alvo", HOST_DEMO]) == 1


def test_demo_conferir_tambem_confere_o_alvo(env_demo, monkeypatch):
    monkeypatch.setattr("srag_agente.demo.conferir.conferir_demo",
                        lambda cfg: pytest.fail("não podia conectar"))  # fmt: skip
    assert cli.main(["demo", "conferir", "--env-arquivo", str(env_demo), "--alvo", "x.y"]) == 2


def test_demo_exportar_usa_a_config_local_e_a_saida(com_senhas, monkeypatch, tmp_path, capsys):
    from srag_agente.demo import pacote

    chamadas = []

    def falso(cfg, saida):
        chamadas.append((cfg.pg_host, saida))
        return pacote.Manifesto(
            1,
            None,
            "abc",
            "m",
            384,
            {
                "referencia": pacote.TabelaPacote("referencia.csv", ("a",), 1, "b" * 64),
                "dicionario_trechos": pacote.TabelaPacote("d.csv", ("a",), 0, "c" * 64),
            },
            "d" * 64,
        )

    monkeypatch.setattr(pacote, "exportar", falso)
    monkeypatch.setattr(pacote, "avisos_pii", lambda pasta: ["texto com telefone"])
    assert cli.main(["demo", "exportar", "--saida", str(tmp_path / "p")]) == 0
    assert chamadas == [("localhost", tmp_path / "p")]
    saida = capsys.readouterr().out
    assert "d" * 64 in saida and "texto com telefone" in saida and "make indexar" in saida


def test_demo_exportar_recusado_sai_com_2(com_senhas, monkeypatch, capsys):
    from srag_agente.demo import pacote

    def recusa(cfg, saida):
        raise pacote.PacoteInvalido(["coluna cs_sexo de gold.indicadores não está na lista"])

    monkeypatch.setattr(pacote, "exportar", recusa)
    assert cli.main(["demo", "exportar"]) == 2
    assert "cs_sexo" in capsys.readouterr().err


# --- (M6) mensagens por fase: antes do COMMIT nada mudou; depois, a gold já foi trocada ---------
def test_ctrl_c_antes_do_commit_diz_que_nada_mudou(env_demo, monkeypatch, capsys):
    def interrompe(*_, **__):
        raise KeyboardInterrupt

    monkeypatch.setattr("srag_agente.demo.carregar.carregar", interrompe)
    assert (
        cli.main(["demo", "carregar", "--env-arquivo", str(env_demo), "--alvo", HOST_DEMO]) == 130
    )
    assert "nada mudou" in capsys.readouterr().err


@pytest.mark.parametrize("interrompida,operacional,codigo", [(True, False, 130), (False, True, 1),
                                                             (False, False, 1)])  # fmt: skip
def test_falha_depois_do_commit_diz_que_a_gold_ja_foi_trocada(
    env_demo, monkeypatch, capsys, interrompida, operacional, codigo
):
    from srag_agente.demo.carregar import CargaConfirmada

    def falha(*_, **__):
        raise CargaConfirmada(
            "a" * 64,
            interrompida=interrompida,
            operacional=operacional,
            causa="OperationalError" if operacional else "RuntimeError",
        )

    monkeypatch.setattr("srag_agente.demo.carregar.carregar", falha)
    assert cli.main(["demo", "carregar", "--env-arquivo", str(env_demo),
                     "--alvo", HOST_DEMO]) == codigo  # fmt: skip
    erro = capsys.readouterr().err
    assert "JÁ foi trocada" in erro and "srag demo conferir" in erro and "nada mudou" not in erro
    assert ("--trocar-senhas" in erro) is operacional
    assert CANARIO not in erro and HOST_DEMO not in erro


def test_demo_carregar_passa_a_flag_de_tls(env_demo, monkeypatch):
    chamadas = []
    monkeypatch.setattr("srag_agente.demo.carregar.carregar",
                        lambda cfg, pasta, **o: chamadas.append(o) or _resumo())  # fmt: skip
    assert cli.main(["demo", "carregar", "--env-arquivo", str(env_demo), "--alvo", HOST_DEMO,
                     "--aceitar-tls-sem-verificacao"]) == 0  # fmt: skip
    assert chamadas[0]["aceitar_tls_sem_verificacao"] is True


# --- (M5) pergunta do dicionário com NUL ou caractere de controle -------------------------------
@pytest.mark.parametrize("modo", ["texto", "vetor"])
@pytest.mark.parametrize("pergunta", ["o que é\x00 UTI?", "UTI\x1b[31m", "campo\x7f"])
def test_dicionario_recusa_caractere_de_controle(com_senhas, monkeypatch, capsys, modo, pergunta):
    monkeypatch.setenv("BUSCA_DICIONARIO", modo)
    monkeypatch.setattr("srag_agente.rag.consulta.abrir_busca",
                        lambda cfg: pytest.fail("não podia abrir a busca"))  # fmt: skip
    assert cli.main(["dicionario", pergunta]) == 2
    assert "caractere de controle" in capsys.readouterr().err


def test_dicionario_aceita_tab_e_nova_linha():
    from srag_agente.rag.consulta import validar_pergunta

    assert validar_pergunta("o que é\tUTI?\nE VACINA_COV?") == "o que é\tUTI?\nE VACINA_COV?"
