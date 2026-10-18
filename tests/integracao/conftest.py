"""Banco da demonstração simulado no Postgres do Compose, como o provedor remoto o terá.

- Dono do banco `srag_dono_demo` SEM superusuário (só CREATEROLE), como o dono no provedor: um
  GRANT ou ALTER que só funcione como superusuário aparece aqui, não na carga de verdade.
- A extensão `vector` é pré-criada como superusuário (na imagem local ela não é "trusted"); o
  `CREATE EXTENSION IF NOT EXISTS` da carga vira nada.
- Os papéis são do cluster: `srag_agente`/`srag_auditor` já existem (criados pelo bootstrap
  local) e o dono recebe ADMIN sobre eles, que no provedor ele ganha ao criá-los por SQL.
  `srag_etl` também existe aqui (no provedor não existirá); um teste confere que ele não tem
  privilégio nenhum no banco da demo.
- Nenhum teste toca banco remoto: tudo recusa rodar se o host do `cfg_teste` não for local, e a
  carga é chamada pela função (com `permitir_local=True`), nunca pela CLI.

Uso: `banco_demo` é o `Config` do `srag_demo_teste` (o `cfg_teste` com outro banco e com o papel
"admin" = dono da demo); `conectar(banco_demo, "auditor")` é a credencial da tela. `carga_demo`
traz também o pacote, o manifesto e o resumo da carga. Testes que mudam limites ou a trilha usam
`banco_descartavel` ou `criar_banco_demo` para não interferir em quem roda ao mesmo tempo.
"""

import hashlib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors, sql
from pydantic import SecretStr

from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.dados.bootstrap import _comando_papel
from srag_agente.demo import HOSTS_LOCAIS, pacote
from srag_agente.demo.carregar import ResumoCarga, carregar
from srag_agente.demo.pacote import Manifesto

BANCO_DEMO = "srag_demo_teste"
DONO_DEMO = "srag_dono_demo"
PREFIXO_DESCARTAVEL = "srag_demo_t_"


@dataclass(frozen=True)
class BancoDemo:
    cfg: Config  # banco da demo; "admin" é o dono sem superusuário
    pacote: Path  # pacote exportado de srag_teste nesta sessão
    manifesto: Manifesto
    resumo: ResumoCarga  # resultado da carga feita pela fixture


def exigir_host_local(cfg: Config) -> None:
    if cfg.pg_host not in HOSTS_LOCAIS:
        pytest.fail(f"testes da demo só rodam contra o Postgres local (PG_HOST em {HOSTS_LOCAIS})")


def senha_do_dono(cfg_teste: Config) -> str:
    """Derivada da senha do admin local: igual em toda sessão de teste (várias sessões ao mesmo
    tempo não trocam a senha umas das outras) e nunca gravada em arquivo."""
    return hashlib.sha256(f"{DONO_DEMO}:{cfg_teste._senha('admin')}".encode()).hexdigest()


def cfg_do_banco(cfg_teste: Config, banco: str) -> Config:
    return cfg_teste.model_copy(
        update={
            "pg_banco": banco,
            "pg_usuario_admin": DONO_DEMO,
            "pg_senha_admin": SecretStr(senha_do_dono(cfg_teste)),
        }
    )


def _preparar_dono(cfg_teste: Config) -> None:
    senha = senha_do_dono(cfg_teste)
    with conectar(cfg_teste, "admin", banco="postgres", autocommit=True) as conn:
        existe = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (DONO_DEMO,)).fetchone()
        if not existe:
            with suppress(errors.DuplicateObject):  # outra sessão criou ao mesmo tempo
                conn.execute(_comando_papel(conn, DONO_DEMO, senha, existe=False))
        try:  # a senha pode ter vindo de um .env antigo: confere entrando
            with psycopg.connect(cfg_do_banco(cfg_teste, "postgres").dsn("admin"),
                                 connect_timeout=5):  # fmt: skip
                pass
        except psycopg.OperationalError:
            conn.execute(_comando_papel(conn, DONO_DEMO, senha, existe=True))
        # Como o dono no provedor: cria papel e administra os que criou; nada de superusuário.
        conn.execute(
            sql.SQL("ALTER ROLE {} NOSUPERUSER CREATEROLE NOCREATEDB NOBYPASSRLS").format(
                sql.Identifier(DONO_DEMO)
            )
        )
        opcoes = "WITH ADMIN OPTION, INHERIT FALSE, SET FALSE"
        if conn.info.server_version < 160000:
            opcoes = "WITH ADMIN OPTION"
        conn.execute(f"GRANT srag_agente, srag_auditor TO {DONO_DEMO} {opcoes}")


def criar_banco_demo(cfg_teste: Config, banco: str) -> Config:
    """Banco vazio com o dono sem superusuário e a extensão `vector` pré-criada."""
    exigir_host_local(cfg_teste)
    _preparar_dono(cfg_teste)
    with conectar(cfg_teste, "admin", banco="postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (banco,)).fetchone():
            with suppress(errors.DuplicateDatabase):  # outra sessão criou ao mesmo tempo
                conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(banco), sql.Identifier(DONO_DEMO)))  # fmt: skip
    with conectar(cfg_teste, "admin", banco=banco, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return cfg_do_banco(cfg_teste, banco)


def apagar_banco_demo(cfg_teste: Config, banco: str) -> None:
    if not banco.startswith(PREFIXO_DESCARTAVEL):  # nunca apaga o banco compartilhado
        raise ValueError(f"{banco} não é descartável")
    with conectar(cfg_teste, "admin", banco="postgres", autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
            sql.Identifier(banco)))  # fmt: skip


TRECHOS_ORIGEM = (
    ("UTI", "Campo UTI: internado em UTI? 1-Sim 2-Não 9-Ignorado"),
    ("VACINA_COV", "Campo VACINA_COV: recebeu vacina COVID-19? 1-Sim 2-Não"),
    (None, 'Trecho sem campo, com vírgula, aspas " e\nquebra de linha'),
)


@pytest.fixture(scope="session")
def banco_origem(cfg_teste, banco_carregado, tmp_path_factory):
    """Gold local de onde a exportação lê: igual ao `srag_teste` (mesma amostra, mesmas views),
    mas só desta sessão. O `srag_teste` é compartilhado e outras sessões de teste o recarregam
    ao mesmo tempo; exportar dele daria um retrato que muda no meio do teste."""
    from srag_agente.dados.bootstrap import preparar_banco
    from srag_agente.dados.carga_raw import carregar_raw
    from srag_agente.dados.transformar import transformar
    from tests.dados.amostra import escrever_csv, gerar_linhas
    from tests.fakes import embedder_falso

    exigir_host_local(cfg_teste)
    nome = f"{PREFIXO_DESCARTAVEL}origem_{uuid4().hex[:8]}"
    preparar_banco(cfg_teste, nome)
    cfg = cfg_teste.model_copy(update={"pg_banco": nome})
    amostra = escrever_csv(tmp_path_factory.mktemp("origem") / "amostra.csv", gerar_linhas())
    vetores = embedder_falso([texto for _, texto in TRECHOS_ORIGEM])
    with conectar(cfg, "etl") as conn:
        carregar_raw(conn, amostra)
        transformar(conn)
        for ordem, ((campo, texto), vetor) in enumerate(zip(TRECHOS_ORIGEM, vetores, strict=True)):
            conn.execute(
                "INSERT INTO gold.dicionario_trechos (id, campo, pagina, texto, embedding) "
                "VALUES (%s, %s, %s, %s, %s::vector)",
                (ordem + 1, campo, ordem + 1, texto, str(vetor)),
            )
    yield cfg
    apagar_banco_demo(cfg_teste, nome)


@pytest.fixture(scope="session")
def carga_demo(cfg_teste, banco_origem, tmp_path_factory) -> BancoDemo:
    """`srag_demo_teste` carregado com o pacote exportado da gold local (com o papel agente)."""
    exigir_host_local(cfg_teste)
    pasta = tmp_path_factory.mktemp("demo") / "pacote"
    manifesto = pacote.exportar(banco_origem, pasta)
    cfg = criar_banco_demo(cfg_teste, BANCO_DEMO)
    resumo = carregar(cfg, pasta, alvo=cfg.pg_host, permitir_local=True)
    return BancoDemo(cfg=cfg, pacote=pasta, manifesto=manifesto, resumo=resumo)


@pytest.fixture(scope="session")
def banco_demo(carga_demo) -> Config:
    """Config do `srag_demo_teste` já carregado (papel "admin" = dono sem superusuário)."""
    return carga_demo.cfg


@pytest.fixture
def banco_descartavel(cfg_teste, banco_carregado):
    """Banco da demo vazio (sem carga), só deste teste; apagado no fim."""
    nome = f"{PREFIXO_DESCARTAVEL}{uuid4().hex[:10]}"
    yield criar_banco_demo(cfg_teste, nome)
    apagar_banco_demo(cfg_teste, nome)
