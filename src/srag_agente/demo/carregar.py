"""Carga do pacote no banco remoto da demonstração (`srag demo carregar`).

Travas contra o banco errado, nesta ordem, antes de qualquer escrita:
1. a configuração vem só do arquivo indicado (`--env-arquivo`), nunca do `.env` local nem de
   variável exportada no shell;
2. o host tem de ser digitado (`--alvo`) e igual ao do arquivo;
3. host local, de rede interna, socket ou com vários endereços é recusado;
4. TLS obrigatório (`require` no mínimo), e a conexão aberta tem de estar de fato cifrada;
5. banco com `raw`/`silver` (o local) ou com a gold em outro formato é recusado, sem DROP.
Tudo roda numa transação só: se algo falha, nada muda. A troca da gold usa DELETE (não
TRUNCATE): quem já está lendo continua lendo o retrato antigo até o COMMIT, sem fila de trava.
Senha: por padrão vai em texto (o provedor recusa hash), só sobre TLS conferido, e só quando o
papel não existe ou com `trocar_senhas`; nenhuma mensagem de erro a repete.
"""

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path

import psycopg
from psycopg import sql

from srag_agente.config import USUARIOS, Config, Papel
from srag_agente.demo import SSLMODES_SEGUROS, TABELAS_DEMO, classificar_host
from srag_agente.demo import host_local as host_local  # a mesma regra da tela, reexportada
from srag_agente.demo.conferir import Conferencia, conferir_catalogo, conferir_demo
from srag_agente.demo.pacote import TIPOS, PacoteLido, ler_pacote
from srag_agente.rag.embeddings import DIMENSAO

PAPEIS_DEMO: tuple[Papel, ...] = ("agente", "auditor")  # o papel de carga nunca vai para lá
# Trava consultiva da carga: duas cargas ao mesmo tempo fazem fila (vizinha da TRAVA_TETO).
TRAVA_CARGA_DEMO = 7_350_303
# Variáveis da libpq que mudariam o destino ou a sessão sem aparecer no DSN.
VARIAVEIS_DE_DESVIO = ("PGHOSTADDR", "PGHOST", "PGSERVICE", "PGOPTIONS")
_SQL_SESSAO = (
    "SET LOCAL search_path = public",  # extensão e tipo `vector` sempre no mesmo esquema
    "SET LOCAL DateStyle = 'ISO, YMD'",
    "SET LOCAL client_encoding = 'UTF8'",
    "SET LOCAL lock_timeout = '30s'",  # não fica parada para sempre atrás de uma leitura longa
)
_TIPO_SQL = {
    "contagem": "integer",
    "data": "date",
    "data_ou_nula": "date",
    "uf": "text",
    "dimensao": "text",
    "categoria": "text",
    "campo": "text",
    "texto_publico": "text",
    "vetor": f"vector({DIMENSAO})",
}
# Colunas (nome, tipo) de cada tabela da gold da demo, na ordem do 01_gold_demo.sql.
COLUNAS_ESPERADAS: dict[str, tuple[tuple[str, str], ...]] = {
    **{t: tuple((c, _TIPO_SQL[TIPOS[c]]) for c in colunas) for t, (colunas, _) in
       TABELAS_DEMO.items()},
    "pacote_demo": (("sha256", "text"), ("gerado_em", "timestamp with time zone"),
                    ("versao_codigo", "text"), ("data_referencia", "date"),
                    ("carregado_em", "timestamp with time zone")),
}  # fmt: skip
_HOSTNAME = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*")


class RecusaCarga(ValueError):  # noqa: N818 (é recusa de uso, não defeito do programa)
    """Uma trava recusou a carga. A mensagem nunca traz senha nem o host do arquivo."""


class CargaConfirmada(Exception):  # noqa: N818 (não é defeito: a carga entrou, a conferência não)
    """A carga já foi confirmada (COMMIT) e a conferência depois dela falhou ou foi interrompida.

    Só leva o sha256, o tipo da causa e se foi de conexão: nunca o texto do erro (host, usuário).
    """

    def __init__(self, sha256: str, *, interrompida: bool, operacional: bool, causa: str) -> None:
        self.sha256, self.interrompida = sha256, interrompida
        self.operacional, self.causa = operacional, causa
        super().__init__(
            f"carga {sha256} confirmada; conferência "
            f"{'interrompida' if interrompida else 'falhou: ' + causa}"
        )


MENSAGEM_TLS_SEM_VERIFICACAO = (
    "A senha dos papéis iria em texto com PG_SSLMODE diferente de verify-full: o certificado do "
    "servidor não seria verificado e quem se pusesse no meio leria a senha. Use "
    "PG_SSLMODE=verify-full com PG_SSLROOTCERT apontando para o arquivo de certificados do "
    "sistema (ex.: /etc/ssl/certs/ca-certificates.crt; o valor 'system' não funcionou com a "
    "libpq do driver) ou, ciente do risco, --aceitar-tls-sem-verificacao."
)


@dataclass(frozen=True)
class ResumoCarga:
    sha256: str
    data_referencia: date | None
    linhas: dict[str, int]
    papeis_criados: tuple[str, ...]
    senhas_enviadas: tuple[str, ...]
    conferencias: tuple[Conferencia, ...]
    avisos: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.conferencias)


# --- travas sem banco ------------------------------------------------------------------------
def _normalizar(host: str | None) -> str:
    host = (host or "").strip().lower().rstrip(".")
    return host[1:-1] if host.startswith("[") and host.endswith("]") else host


def _host_valido(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return bool(_HOSTNAME.fullmatch(host))
    return True


def conferir_alvo(
    cfg: Config,
    alvo: str | None,
    permitir_local: bool = False,
    *,
    ambiente: Mapping[str, str] | None = None,
) -> None:
    """Recusa (RecusaCarga) o que não for o banco remoto que quem carrega digitou.

    `permitir_local` existe só para os testes (a CLI não tem a opção): libera host local e TLS,
    nunca a confirmação digitada nem o host único.
    """
    ambiente = os.environ if ambiente is None else ambiente
    host = _normalizar(cfg.pg_host)
    if not _normalizar(alvo):
        raise RecusaCarga("--alvo é obrigatório: digite o host do banco que vai receber a carga.")
    if _normalizar(alvo) != host:
        raise RecusaCarga(
            "--alvo não confere com o PG_HOST do arquivo informado. Confira o arquivo e digite "
            "de novo o host do banco que vai receber a carga."
        )
    if not _host_valido(host):
        raise RecusaCarga("PG_HOST precisa ser um único nome de host ou IP (sem socket, vírgula "
                          "ou espaço).")  # fmt: skip
    if desvio := [v for v in VARIAVEIS_DE_DESVIO if ambiente.get(v)]:
        raise RecusaCarga(f"{', '.join(desvio)} definida no ambiente mudaria a conexão sem "
                          "aparecer na configuração; remova antes de carregar.")  # fmt: skip
    if permitir_local:
        return
    situacao = classificar_host(host)
    if situacao == "sem_resolucao":
        raise RecusaCarga("PG_HOST não resolve (DNS): confira o nome do host no arquivo e a "
                          "conexão com a internet.")  # fmt: skip
    if situacao == "local":
        raise RecusaCarga("PG_HOST é a máquina local ou uma rede interna; a carga da demo só vai "
                          "para o banco remoto.")  # fmt: skip
    if cfg.pg_sslmode not in SSLMODES_SEGUROS:
        raise RecusaCarga("PG_SSLMODE precisa ser require, verify-ca ou verify-full (a senha e "
                          "os dados vão pela internet).")  # fmt: skip


def conferir_env_arquivo(caminho: Path | str | None) -> Path:
    """O arquivo de configuração da carga: obrigatório, existente e nunca um `.env`."""
    if not caminho:
        raise RecusaCarga("--env-arquivo é obrigatório (ex.: --env-arquivo .env.demo).")
    arquivo = Path(caminho).expanduser()
    padrao = Path(Config.model_config["env_file"]).resolve()
    if arquivo.name == ".env" or arquivo.resolve() == padrao:
        raise RecusaCarga("O .env local nunca é a fonte da carga da demo; use o .env.demo "
                          "(modelo em .env.demo.example).")  # fmt: skip
    if not arquivo.is_file():
        raise RecusaCarga(f"--env-arquivo {arquivo} não é um arquivo.")
    return arquivo


class _ConfigDoArquivo(Config):
    """Config que lê só os argumentos e o arquivo indicado: variável exportada no shell não
    troca host, TLS nem senha da carga (ex.: a senha local de um papel)."""

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings, dotenv_settings)


def ler_config_demo(arquivo: Path) -> Config:
    """Config do banco remoto. Modo demo: só agente e auditor obrigatórios; a senha do dono é
    pedida pela carga (`_senha("admin")`) e não pela conferência."""
    return _ConfigDoArquivo(_env_file=arquivo, demo_online=True)


# --- passos com banco --------------------------------------------------------------------------
def ler_sql_demo(nome: str) -> str:
    return (files("srag_agente.demo.sql") / nome).read_text(encoding="utf-8")


def _colunas_atuais(conn: psycopg.Connection, tabela: str) -> list[tuple[str, str]] | None:
    """(nome, tipo) de gold.<tabela> se for tabela; None se não existir; [] se não for tabela."""
    linha = conn.execute(
        "SELECT c.oid, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'gold' AND c.relname = %s",
        (tabela,),
    ).fetchone()
    if linha is None:
        return None
    if linha[1] != "r":
        return []
    return [
        (nome, tipo)
        for nome, tipo in conn.execute(
            "SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
            (linha[0],),
        )
    ]


def _recusar_banco_errado(conn: psycopg.Connection) -> None:
    if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname IN ('raw', 'silver')").fetchone():
        raise RecusaCarga("Este banco tem os esquemas raw/silver: parece o banco local. Nada foi "
                          "alterado.")  # fmt: skip
    divergentes = []
    for tabela, esperadas in COLUNAS_ESPERADAS.items():
        atuais = _colunas_atuais(conn, tabela)
        if atuais is not None and atuais != list(esperadas):
            divergentes.append(f"gold.{tabela}")
    if divergentes:
        raise RecusaCarga(
            f"Esquema divergente em {', '.join(divergentes)} (colunas, tipos ou view no lugar de "
            "tabela). Nada foi apagado nem alterado; confira o banco antes de carregar."
        )


def _preparar_papeis(conn: psycopg.Connection, senhas: dict[str, str], *, em_hash: bool,
                     trocar: bool, texto_permitido: bool = True,
                     ) -> tuple[list[str], list[str]]:  # fmt: skip
    from srag_agente.dados.bootstrap import _comando_papel

    criados, enviadas = [], []
    for papel in PAPEIS_DEMO:
        usuario = USUARIOS[papel]
        existe = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (usuario,)).fetchone()
        if existe and not trocar:
            continue  # recarregar o pacote não reenvia senha
        if not em_hash and not texto_permitido:
            raise RecusaCarga(MENSAGEM_TLS_SEM_VERIFICACAO)
        comando = _comando_papel(conn, usuario, senhas[papel], existe=bool(existe), em_hash=em_hash)
        recusa = None
        try:
            conn.execute(comando)
        except psycopg.Error as erro:
            # O texto do servidor pode repetir o comando (LINE 1: ... PASSWORD '...'): só a
            # primeira linha, sem nada entre aspas e sem senha. A recusa nasce FORA deste
            # bloco, para o erro original (com a senha) não ficar encadeado no traceback.
            principal = (erro.diag.message_primary or str(erro)).split("\n", 1)[0]
            for senha in senhas.values():
                principal = principal.replace(senha, "***")
            principal = re.sub(r"(\"[^\"]*\"|'[^']*')", "…", principal)[:200]
            recusa = (f"{'ALTER' if existe else 'CREATE'} ROLE {usuario} recusado pelo servidor "
                      f"(SQLSTATE {erro.sqlstate or '-'}): {principal}")  # fmt: skip
        if recusa:
            raise RecusaCarga(recusa)
        enviadas.append(usuario)
        if not existe:
            criados.append(usuario)
    return criados, enviadas


def _substituir_gold(conn: psycopg.Connection, lido: PacoteLido) -> None:
    """Troca o retrato inteiro com os bytes já conferidos, sem bloquear leitura.

    DELETE, não TRUNCATE: o TRUNCATE pede ACCESS EXCLUSIVE, espera o relatório que está lendo e
    põe na fila todo leitor novo, que cai no statement_timeout do agente (5 s) depois de ter
    gastado crédito. O DELETE convive com a leitura: até o COMMIT, todos veem o retrato antigo;
    depois, o novo. As tabelas são pequenas (agregados), o custo do DELETE não pesa.
    """
    for tabela in TABELAS_DEMO:
        conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier("gold", tabela)))
    # HEADER MATCH (PG 15+) faz o próprio servidor conferir o cabeçalho com a lista de colunas.
    cabecalho = sql.SQL("MATCH" if conn.info.server_version >= 150000 else "true")
    for tabela, (colunas, _) in TABELAS_DEMO.items():
        comando = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER {})").format(
            sql.Identifier("gold", tabela), sql.SQL(", ").join(map(sql.Identifier, colunas)),
            cabecalho,
        )  # fmt: skip
        with conn.cursor() as cursor, cursor.copy(comando) as copia:
            copia.write(lido.conteudos[tabela])


def _conferir_dentro(conn: psycopg.Connection, lido: PacoteLido) -> list[str]:
    """Conferências antes do COMMIT: qualquer problema desfaz a carga inteira."""
    problemas = []
    for tabela in TABELAS_DEMO:
        total = conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("gold", tabela))
        ).fetchone()[0]
        if total != lido.manifesto.tabelas[tabela].linhas:
            problemas.append(f"gold.{tabela}: {total} linhas, o manifesto diz "
                             f"{lido.manifesto.tabelas[tabela].linhas}")  # fmt: skip
    for tabela, esperadas in COLUNAS_ESPERADAS.items():
        if _colunas_atuais(conn, tabela) != list(esperadas):
            problemas.append(f"gold.{tabela}: colunas ou tipos diferentes do esperado")
    # Extensão fora da lista não desfaz a carga (o provedor pode ter a dele): vira aviso aqui e
    # falha na conferência depois do COMMIT, para o dono decidir.
    problemas += [f"{c.tentativa}: {c.obtido}"
                  for c in conferir_catalogo(conn, incluir_extensoes=False)
                  if not c.ok]  # fmt: skip
    privilegios = conn.execute(
        "SELECT r, has_schema_privilege(r, 'public', 'CREATE'), "
        "has_database_privilege(r, current_database(), 'TEMPORARY') "
        "FROM unnest(%s::text[]) AS r",
        ([USUARIOS[p] for p in PAPEIS_DEMO],),
    ).fetchall()
    problemas += [f"{r}: pode criar objeto em public ou tabela temporária"
                  for r, criar, temporaria in privilegios if criar or temporaria]  # fmt: skip
    if conn.execute("SELECT count(*) FROM auditoria.limites_demo").fetchone()[0] != 1:
        problemas.append("auditoria.limites_demo sem a linha de limites")
    return problemas


def carregar(
    cfg: Config,
    pasta: Path,
    *,
    alvo: str,
    senha_em_texto: bool = True,
    trocar_senhas: bool = False,
    permitir_local: bool = False,
    aceitar_tls_sem_verificacao: bool = False,
) -> ResumoCarga:
    """Passos da §2.3 do desenho numa transação do dono do banco; depois, `conferir_demo`.

    Senha em texto só sai com `PG_SSLMODE=verify-full` ou com `aceitar_tls_sem_verificacao`.
    Falha depois do COMMIT (na conferência) vira `CargaConfirmada`: a gold já foi trocada.
    Atenção: chamada direto (sem a CLI), o erro bruto de conexão do psycopg traz host e usuário
    (nunca a senha); quem mascara para o terminal é a CLI.
    """
    from srag_agente.banco import conectar
    from srag_agente.dados.bootstrap import ler_sql

    conferir_alvo(cfg, alvo, permitir_local)
    lido = ler_pacote(pasta)  # sha256, colunas, linhas e domínios, antes de abrir conexão
    senhas = {papel: cfg._senha(papel) for papel in PAPEIS_DEMO}
    texto_permitido = (not senha_em_texto or cfg.pg_sslmode == "verify-full"
                       or aceitar_tls_sem_verificacao)  # fmt: skip
    if trocar_senhas and not texto_permitido:  # já se sabe que a senha iria: nem conecta
        raise RecusaCarga(MENSAGEM_TLS_SEM_VERIFICACAO)
    with conectar(cfg, "admin") as conn:
        if not permitir_local and not conn.pgconn.ssl_in_use:
            raise RecusaCarga("A conexão com o banco não está cifrada (TLS); nada foi enviado.")
        with conn.transaction():
            for comando in _SQL_SESSAO:
                conn.execute(comando)
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (TRAVA_CARGA_DEMO,))
            _recusar_banco_errado(conn)
            criados, enviadas = _preparar_papeis(conn, senhas, em_hash=not senha_em_texto,
                                                 trocar=trocar_senhas,
                                                 texto_permitido=texto_permitido)  # fmt: skip
            conn.execute(ler_sql_demo("00_estrutura_demo.sql"))
            conn.execute(ler_sql_demo("01_gold_demo.sql"))
            _substituir_gold(conn, lido)
            referencia = conn.execute("SELECT data_referencia FROM gold.referencia").fetchone()[0]
            manifesto = lido.manifesto
            conn.execute(
                "INSERT INTO gold.pacote_demo (sha256, gerado_em, versao_codigo, data_referencia) "
                "VALUES (%s, %s, %s, %s)",
                (manifesto.sha256, manifesto.gerado_em, manifesto.versao_codigo, referencia),
            )
            conn.execute(ler_sql("04_auditoria.sql"))  # o mesmo da carga local, sem alteração
            conn.execute(ler_sql_demo("02_teto_demo.sql"))
            if problemas := _conferir_dentro(conn, lido):
                raise RecusaCarga("Conferência da carga falhou; nada foi alterado:\n"
                                  + "\n".join(f"  - {p}" for p in problemas))  # fmt: skip
            avisos = tuple(
                f"extensão {nome} instalada no banco (esperado: plpgsql e vector)"
                for (nome,) in conn.execute(
                    "SELECT extname FROM pg_extension WHERE extname NOT IN ('plpgsql', 'vector') "
                    "ORDER BY 1"
                )
            )
    # Daqui em diante a gold JÁ foi trocada: falha ou Ctrl+C não pode dizer "nada mudou".
    try:
        conferencias = tuple(conferir_demo(cfg))  # com as credenciais da tela
    except KeyboardInterrupt:
        conferencias = None
        causa = ("KeyboardInterrupt", True, False)
    except Exception as erro:  # vira CargaConfirmada, sem o texto do erro
        conferencias = None
        causa = (type(erro).__name__, False, isinstance(erro, psycopg.OperationalError))
    if conferencias is None:
        raise CargaConfirmada(manifesto.sha256, interrompida=causa[1], operacional=causa[2],
                              causa=causa[0])  # fmt: skip
    return ResumoCarga(
        sha256=manifesto.sha256,
        data_referencia=referencia,
        linhas={t: m.linhas for t, m in manifesto.tabelas.items()},
        papeis_criados=tuple(criados),
        senhas_enviadas=tuple(enviadas),
        conferencias=conferencias,
        avisos=avisos,
    )
