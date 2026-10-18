"""Travas contra carregar no banco errado e contrato dos SQL da demo. Sem banco."""

import re
import socket
from importlib.resources import files
from pathlib import Path

import pytest

from srag_agente.config import RAIZ_PROJETO, Config
from srag_agente.demo import FUSO_DEMO, HOSTS_LOCAIS, SQLSTATE_TETO, TRAVA_TETO, carregar
from srag_agente.demo.carregar import RecusaCarga, conferir_alvo, conferir_env_arquivo
from srag_agente.metricas.repositorio import TEMPO_MAXIMO

HOST = "ep-exemplo-123.sa-east-1.aws.exemplo.tech"
SENHA_CANARIO = "canario-senha-Zq81"
PAPEIS = ("agente", "auditor")
SQL_DEMO = ("00_estrutura_demo.sql", "01_gold_demo.sql", "02_teto_demo.sql")


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    for campo in Config.model_fields:
        monkeypatch.delenv(campo.upper(), raising=False)
    for variavel in ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS", "PGHOST"):
        monkeypatch.delenv(variavel, raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", resolver_falso)


# DNS de mentira: o teste nunca depende de rede. Nome fora da tabela não resolve.
DNS = {
    HOST: ["52.67.10.20"],
    "ep-outro.sa-east-1.aws.exemplo.tech": ["52.67.10.21"],
    "db.exemplo.com.br": ["8.8.4.4"],
    "127.0.0.1.nip.io": ["127.0.0.1"],
    "localtest.me": ["127.0.0.1", "::1"],
    "misto.exemplo.tech": ["52.67.10.20", "10.0.0.7"],
    "mapeado.exemplo.tech": ["::ffff:127.0.0.1"],
    "multicast.exemplo.tech": ["224.0.0.5"],
}


def resolver_falso(nome, *_, **__):
    if nome not in DNS:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "",
             (ip, 0)) for ip in DNS[nome]]  # fmt: skip


def cfg(host: str = HOST, sslmode: str | None = "verify-full") -> Config:
    return Config(
        _env_file=None,
        demo_online=True,
        pg_host=host,
        pg_sslmode=sslmode,
        pg_senha_admin=SENHA_CANARIO,
        pg_senha_agente=SENHA_CANARIO + "a",
        pg_senha_auditor=SENHA_CANARIO + "b",
    )


def _sql(nome: str) -> str:
    return (files("srag_agente.demo.sql") / nome).read_text(encoding="utf-8")


# --- alvo digitado ---------------------------------------------------------------------------
@pytest.mark.parametrize("alvo", [HOST, HOST.upper(), f"  {HOST} ", f"{HOST}."])
def test_alvo_igual_ao_host_passa(alvo):
    conferir_alvo(cfg(), alvo)


@pytest.mark.parametrize("alvo", ["", "   ", None, "ep-outro.sa-east-1.aws.exemplo.tech",
                                  HOST + ".x", HOST[:-1]])  # fmt: skip
def test_alvo_diferente_ou_vazio_e_recusado(alvo):
    with pytest.raises(RecusaCarga):
        conferir_alvo(cfg(), alvo)


def test_alvo_diferente_e_recusado_mesmo_com_permitir_local():
    with pytest.raises(RecusaCarga):
        conferir_alvo(cfg("localhost"), "127.0.0.1", permitir_local=True)


# --- host local ------------------------------------------------------------------------------
LOCAIS = [
    *sorted(HOSTS_LOCAIS),
    "LOCALHOST", "localhost.", "maquina.local", "srag.localhost", "127.0.0.2", "0.0.0.0",
    "10.0.0.5", "172.17.0.1", "192.168.1.10", "169.254.1.1", "::ffff:127.0.0.1", "[::1]",
]  # fmt: skip


@pytest.mark.parametrize("host", LOCAIS)
def test_host_local_e_recusado(host):
    with pytest.raises(RecusaCarga, match="local"):
        conferir_alvo(cfg(host), host)


@pytest.mark.parametrize("host", LOCAIS)
def test_host_local_passa_so_com_permitir_local(host):
    conferir_alvo(cfg(host, sslmode=None), host, permitir_local=True)


@pytest.mark.parametrize("host", ["/var/run/postgresql", "h1.exemplo.tech,h2.exemplo.tech",
                                  "host com espaço", "host=x"])  # fmt: skip
def test_host_que_nao_e_um_nome_unico_e_recusado(host):
    with pytest.raises(RecusaCarga):
        conferir_alvo(cfg(host), host)
    with pytest.raises(RecusaCarga):  # nem nos testes: socket ou vários hosts não são alvo
        conferir_alvo(cfg(host), host, permitir_local=True)


@pytest.mark.parametrize("host", [HOST, "db.exemplo.com.br", "8.8.8.8"])
def test_host_remoto_nao_e_local(host):
    assert not carregar.host_local(host)


# --- TLS -------------------------------------------------------------------------------------
@pytest.mark.parametrize("sslmode", [None, "disable", "allow", "prefer"])
def test_tls_fraco_e_recusado(sslmode):
    with pytest.raises(RecusaCarga, match="PG_SSLMODE"):
        conferir_alvo(cfg(sslmode=sslmode), HOST)


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_tls_seguro_passa(sslmode):
    conferir_alvo(cfg(sslmode=sslmode), HOST)


# --- ambiente que desviaria a conexão --------------------------------------------------------
@pytest.mark.parametrize("variavel", ["PGHOSTADDR", "PGSERVICE", "PGOPTIONS", "PGHOST"])
def test_variavel_libpq_que_desvia_a_conexao_e_recusada(variavel):
    with pytest.raises(RecusaCarga, match=variavel):
        conferir_alvo(cfg(), HOST, ambiente={variavel: "127.0.0.1"})
    with pytest.raises(RecusaCarga, match=variavel):
        conferir_alvo(cfg("localhost", None), "localhost", permitir_local=True,
                      ambiente={variavel: "x"})  # fmt: skip


def test_variavel_do_ambiente_real_e_lida(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    with pytest.raises(RecusaCarga, match="PGHOSTADDR"):
        conferir_alvo(cfg(), HOST)


# --- mensagens sem segredo -------------------------------------------------------------------
@pytest.mark.parametrize(
    "config,alvo",
    [(cfg(), "outro.exemplo.tech"), (cfg("localhost"), "localhost"),
     (cfg(sslmode="prefer"), HOST), (cfg(), "")],
)  # fmt: skip
def test_mensagem_de_recusa_nao_tem_senha_nem_host_do_arquivo(config, alvo):
    with pytest.raises(RecusaCarga) as recusa:
        conferir_alvo(config, alvo)
    texto = str(recusa.value)
    assert SENHA_CANARIO not in texto
    assert HOST not in texto  # o host do arquivo não é repetido (quem digitou já sabe o seu)


# --- arquivo de configuração -----------------------------------------------------------------
def test_env_arquivo_e_obrigatorio():
    with pytest.raises(RecusaCarga, match="--env-arquivo"):
        conferir_env_arquivo(None)


def test_env_padrao_e_recusado(monkeypatch, tmp_path):
    padrao = RAIZ_PROJETO / ".env"
    with pytest.raises(RecusaCarga, match=r"\.env"):
        conferir_env_arquivo(padrao)
    monkeypatch.chdir(RAIZ_PROJETO)
    with pytest.raises(RecusaCarga, match=r"\.env"):
        conferir_env_arquivo(Path(".env"))
    with pytest.raises(RecusaCarga, match=r"\.env"):
        conferir_env_arquivo(Path("docs/../.env"))
    outro = tmp_path / ".env"  # qualquer arquivo chamado .env: é o de algum projeto local
    outro.write_text("PG_HOST=x\n", encoding="utf-8")
    with pytest.raises(RecusaCarga, match=r"\.env"):
        conferir_env_arquivo(outro)


def test_env_arquivo_inexistente_ou_pasta_e_recusado(tmp_path):
    with pytest.raises(RecusaCarga):
        conferir_env_arquivo(tmp_path / "nao-existe")
    with pytest.raises(RecusaCarga):
        conferir_env_arquivo(tmp_path)


def test_env_arquivo_valido_passa(tmp_path):
    arquivo = tmp_path / ".env.demo"
    arquivo.write_text("PG_HOST=x\n", encoding="utf-8")
    assert conferir_env_arquivo(arquivo) == arquivo


def _arquivo_demo(tmp_path: Path, extra: str = "") -> Path:
    arquivo = tmp_path / ".env.demo"
    arquivo.write_text(
        f"PG_HOST={HOST}\nPG_PORTA=5432\nPG_BANCO=demo\nPG_USUARIO_ADMIN=dono\n"
        "PG_SSLMODE=verify-full\nPG_SENHA_ADMIN=s-admin\nPG_SENHA_AGENTE=s-agente\n"
        f"PG_SENHA_AUDITOR=s-auditor\n{extra}",
        encoding="utf-8",
    )
    return arquivo


def test_config_da_demo_vem_so_do_arquivo(tmp_path, monkeypatch):
    # variável exportada no shell (ex.: a senha local) não decide host nem senha da carga
    monkeypatch.setenv("PG_HOST", "localhost")
    monkeypatch.setenv("PG_SENHA_AGENTE", "senha-local")
    monkeypatch.setenv("PG_SSLMODE", "disable")
    config = carregar.ler_config_demo(_arquivo_demo(tmp_path))
    assert config.pg_host == HOST and config.pg_sslmode == "verify-full"
    assert config._senha("agente") == "s-agente"
    assert config.pg_usuario_admin == "dono" and config.pg_banco == "demo"


def test_config_da_demo_nao_exige_a_senha_do_etl(tmp_path):
    config = carregar.ler_config_demo(_arquivo_demo(tmp_path))
    assert config.pg_senha_etl is None
    assert isinstance(config, Config)


def test_config_da_demo_sem_senha_do_dono_falha_ao_pedir(tmp_path):
    arquivo = _arquivo_demo(tmp_path)
    arquivo.write_text(arquivo.read_text(encoding="utf-8").replace("PG_SENHA_ADMIN=s-admin\n", ""),
                       encoding="utf-8")  # fmt: skip
    from srag_agente.agente.llm import ConfiguracaoInvalida

    with pytest.raises(ConfiguracaoInvalida, match="PG_SENHA_ADMIN"):
        carregar.ler_config_demo(arquivo)._senha("admin")


# --- papéis -----------------------------------------------------------------------------------
def test_carga_da_demo_so_cuida_de_agente_e_auditor():
    assert carregar.PAPEIS_DEMO == ("agente", "auditor")


# --- contrato dos SQL da demo -----------------------------------------------------------------
def test_gatilho_usa_o_sqlstate_a_trava_e_o_fuso_do_contrato():
    teto = _sql("02_teto_demo.sql")
    assert set(re.findall(r"ERRCODE = '(\w+)'", teto)) == {SQLSTATE_TETO}
    assert f"pg_advisory_xact_lock({TRAVA_TETO})" in teto
    assert f"'{FUSO_DEMO}'" in teto


def test_semente_dos_limites_e_a_politica_proposta():
    teto = _sql("02_teto_demo.sql")
    assert re.search(r"VALUES \(1, 10, 40, 1\.00, 'America/Sao_Paulo'\)", teto)


def test_sql_da_demo_nao_apaga_nem_cria_o_papel_de_carga():
    for nome in SQL_DEMO:
        texto = _sql(nome)
        assert "srag_etl" not in texto, nome
        assert not re.search(r"\bDROP\b", texto, re.IGNORECASE), nome
        assert not re.search(r"\b(raw|silver)\.", texto), nome


def test_agente_da_demo_tem_o_mesmo_tempo_maximo_do_repositorio():
    assert f"ALTER ROLE srag_agente SET statement_timeout = '{TEMPO_MAXIMO}'" in _sql(SQL_DEMO[0])


def test_tipos_esperados_cobrem_toda_coluna_da_gold_demo():
    gold = _sql("01_gold_demo.sql")
    for tabela, colunas in carregar.COLUNAS_ESPERADAS.items():
        assert f"gold.{tabela} (" in gold, tabela
        for coluna, _ in colunas:
            assert re.search(rf"\b{coluna}\b", gold), (tabela, coluna)


# --- senha do papel: hash no cliente (local) ou texto sobre TLS (provedor que recusa hash) -----
def test_comando_de_papel_em_texto_leva_a_senha_como_literal():
    from srag_agente.dados.bootstrap import _comando_papel

    criar = _comando_papel(None, "srag_agente", "s'nh@", existe=False, em_hash=False)
    alterar = _comando_papel(None, "srag_auditor", "x", existe=True, em_hash=False)
    assert criar.as_string() == "CREATE ROLE \"srag_agente\" LOGIN PASSWORD 's''nh@'"
    assert alterar.as_string() == "ALTER ROLE \"srag_auditor\" WITH LOGIN PASSWORD 'x'"


# --- TLS pelo efeito: a conexão aberta tem de estar cifrada --------------------------------------
class _ConexaoSemTls:
    """Dublê: conexão aberta sem TLS (ex.: libpq que ignorou o sslmode). Registra comandos."""

    def __init__(self):
        self.comandos = []
        self.pgconn = type("PGconn", (), {"ssl_in_use": False})()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, comando, *_):
        self.comandos.append(comando)

    def transaction(self):
        raise AssertionError("não podia abrir transação sem TLS")


def test_carga_recusa_conexao_que_nao_esta_cifrada(monkeypatch):
    conexao = _ConexaoSemTls()
    monkeypatch.setattr("srag_agente.banco.conectar", lambda *a, **k: conexao)
    monkeypatch.setattr(carregar, "ler_pacote", lambda pasta: None)
    with pytest.raises(RecusaCarga, match="cifrada"):
        carregar.carregar(cfg(), Path("pacote"), alvo=HOST)
    assert conexao.comandos == []  # nem papel, nem senha, nem SQL


# --- (6) uma regra só de "host local", para a carga e para a tela ---------------------------------
def test_regra_de_host_local_mora_no_contrato_e_a_carga_usa_a_mesma():
    import srag_agente.demo as contrato

    assert carregar.host_local is contrato.host_local


@pytest.mark.parametrize("host", ["", "  ", None, "/var/run/postgresql", "h1.exemplo.tech,x",
                                  "127.0.0.1", "127.1.2.3", "[::1]", "::ffff:10.0.0.1", "0.0.0.0",
                                  "srag.localhost.", "Maquina.Local"])  # fmt: skip
def test_regra_unica_trata_socket_lista_e_loopback_como_local(host):
    from srag_agente.demo import host_local

    assert host_local(host)


# --- (5) senha em texto é o padrão da carga, e nenhuma mensagem a repete ------------------------
def test_carga_manda_senha_em_texto_por_padrao():
    import inspect

    assert inspect.signature(carregar.carregar).parameters["senha_em_texto"].default is True


class _ConexaoQueRecusaPapel:
    """Dublê: o servidor recusa o CREATE ROLE e repete o comando na mensagem (LINE 1: ...)."""

    def __init__(self, senha):
        self.senha = senha

    def execute(self, comando, *_):
        texto = comando if isinstance(comando, str) else comando.as_string()
        if texto.startswith("SELECT 1 FROM pg_roles"):
            return type("Cursor", (), {"fetchone": lambda self: None})()
        import psycopg

        raise psycopg.errors.InvalidParameterValue(
            f"senha recusada\nLINE 1: {texto}\n  PASSWORD '{self.senha}'"
        )


def test_create_role_recusado_nao_repete_a_senha():
    senha = SENHA_CANARIO + "-papel"
    with pytest.raises(RecusaCarga) as recusa:
        carregar._preparar_papeis(_ConexaoQueRecusaPapel(senha), dict.fromkeys(PAPEIS, senha),
                                  em_hash=False, trocar=False)  # fmt: skip
    erro = recusa.value
    texto = str(erro) + repr(erro) + repr(erro.args)
    assert senha not in texto and "srag_agente" in texto and "22023" in texto
    assert erro.__context__ is None and erro.__cause__ is None  # nem no traceback encadeado


# --- (I1) formas numéricas de loopback e nomes que resolvem para a própria máquina -------------
@pytest.mark.parametrize("host", ["127.1", "2130706433", "0x7f000001", "0177.0.0.1", "224.0.0.1",
                                  "127.0.0.1.nip.io", "localtest.me", "misto.exemplo.tech",
                                  "mapeado.exemplo.tech", "multicast.exemplo.tech"])  # fmt: skip
def test_loopback_disfarcado_e_local(host):
    from srag_agente.demo import host_local

    assert host_local(host)
    with pytest.raises(RecusaCarga, match="local"):
        conferir_alvo(cfg(host), host)


def test_nome_que_nao_resolve_e_conservador_e_a_carga_diz_por_que():
    from srag_agente.demo import host_local

    assert host_local("nao-resolve.exemplo.tech")  # na dúvida, não é o banco remoto
    with pytest.raises(RecusaCarga, match="não resolve") as recusa:
        conferir_alvo(cfg("nao-resolve.exemplo.tech"), "nao-resolve.exemplo.tech")
    assert SENHA_CANARIO not in str(recusa.value)


def test_nome_publico_que_resolve_nao_e_local():
    from srag_agente.demo import host_local

    assert not host_local(HOST) and not host_local("db.exemplo.com.br")


# --- (M7) senha em texto só com o servidor verificado -------------------------------------------
class _ConexaoPapelNovo:
    def __init__(self):
        self.comandos = []

    def execute(self, comando, *_):
        texto = comando if isinstance(comando, str) else comando.as_string()
        self.comandos.append(texto)
        return type("Cursor", (), {"fetchone": lambda self: None})()


def test_senha_em_texto_sem_verify_full_e_recusada():
    conexao = _ConexaoPapelNovo()
    with pytest.raises(RecusaCarga, match="verify-full"):
        carregar._preparar_papeis(conexao, dict.fromkeys(PAPEIS, SENHA_CANARIO), em_hash=False,
                                  trocar=False, texto_permitido=False)  # fmt: skip
    assert not [c for c in conexao.comandos if "PASSWORD" in c]


def test_trocar_senhas_sem_verify_full_recusa_antes_de_conectar(monkeypatch):
    monkeypatch.setattr("srag_agente.banco.conectar",
                        lambda *a, **k: pytest.fail("não podia conectar"))  # fmt: skip
    monkeypatch.setattr(carregar, "ler_pacote", lambda pasta: None)
    with pytest.raises(RecusaCarga, match="--aceitar-tls-sem-verificacao"):
        carregar.carregar(cfg(sslmode="require"), Path("p"), alvo=HOST, trocar_senhas=True)


def test_carga_aceita_tls_sem_verificacao_so_com_a_flag():
    import inspect

    assert inspect.signature(carregar.carregar).parameters[
        "aceitar_tls_sem_verificacao"].default is False  # fmt: skip
