"""Prova pelo efeito, com as credenciais da tela (agente e auditor), que o banco da demo está como
deve: cada papel alcança só o que precisa, e o catálogo não tem nada além da gold e da auditoria.

Nada aqui escreve. Cada tentativa de escrita roda numa transação desfeita no fim
(`force_rollback`), e o banco a recusa já no planejamento (privilégio), antes de tocar em linha.
O mesmo código roda nos testes de integração e contra o banco remoto.
"""

from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from psycopg import errors

from srag_agente.config import Config
from srag_agente.demo import TABELAS_DEMO

GATILHO_TETO = "teto_diario_de_execucoes"
OBJETOS_ESPERADOS: dict[str, frozenset[str]] = {
    "gold": frozenset({*TABELAS_DEMO, "pacote_demo"}),
    "auditoria": frozenset({"execucoes", "eventos", "limites_demo"}),
}
_PRIVILEGIO = (errors.InsufficientPrivilege,)
_SOMENTE_LEITURA = (errors.ReadOnlySqlTransaction, errors.InsufficientPrivilege)

# Objeto que pertence a extensão (deptype 'e': a do provedor, a `vector`) não é da demo nem dado
# nosso; fica fora. O que sobrar é listado por nome e tipo para o dono do banco decidir.
SQL_FORA_DA_DEMO = r"""
SELECT n.nspname, c.relname, c.relkind
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast', 'gold', 'auditoria')
  AND n.nspname NOT LIKE 'pg\_temp\_%' AND n.nspname NOT LIKE 'pg\_toast\_temp\_%'
  AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                  AND d.objid = c.oid AND d.deptype = 'e')
ORDER BY 1, 2
"""
TIPOS_DE_OBJETO = {"r": "tabela", "v": "view", "m": "view materializada",
                   "p": "tabela particionada", "f": "tabela externa"}  # fmt: skip
SQL_RAW_SILVER = "SELECT nspname FROM pg_namespace WHERE nspname IN ('raw', 'silver')"
SQL_PODERES = """
SELECT r.rolname, r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolbypassrls, r.rolreplication,
       array(SELECT b.rolname FROM pg_auth_members m JOIN pg_roles b ON b.oid = m.roleid
             WHERE m.member = r.oid ORDER BY 1) AS membro_de
FROM pg_roles r WHERE r.rolname IN ('srag_agente', 'srag_auditor') ORDER BY 1
"""
SQL_OBJETOS_DA_DEMO = """
SELECT n.nspname, c.relname, c.relkind
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('gold', 'auditoria') AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
"""
# Pelo catálogo, sem `::regclass`: resolver o nome exigiria USAGE no esquema, que o agente não tem.
SQL_GATILHO = """
SELECT t.tgenabled FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE t.tgname = %s AND NOT t.tgisinternal AND n.nspname = 'auditoria' AND c.relname = 'execucoes'
"""


SQL_EXTENSOES = (
    "SELECT extname FROM pg_extension WHERE extname NOT IN ('plpgsql', 'vector') ORDER BY 1"
)
# Configuração de sessão dos papéis neste banco (geral ou só deste banco).
SQL_CONFIGURACOES = """
SELECT r.rolname, s.setdatabase = 0, c
FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole, unnest(s.setconfig) AS c
WHERE r.rolname IN ('srag_agente', 'srag_auditor')
  AND s.setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))
"""
# (papel, chave, valor, vale para todos os bancos): o que 00_estrutura_demo.sql deixa.
CONFIGURACOES_ESPERADAS = frozenset({
    ("srag_agente", "default_transaction_read_only", "on", True),
    ("srag_agente", "statement_timeout", "5s", True),
})  # fmt: skip


@dataclass(frozen=True)
class Conferencia:
    papel: str  # agente, auditor ou catalogo
    tentativa: str
    esperado: str
    ok: bool
    obtido: str


def _recusa(conn: psycopg.Connection, comando: str, aceitos: tuple[type[Exception], ...]):
    """(ok, obtido): ok se o banco recusou com um dos erros aceitos. Nunca confirma nada."""
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(comando)
    except aceitos as erro:
        return True, type(erro).__name__
    except psycopg.Error as erro:
        return False, f"{type(erro).__name__} (esperado outro erro)"
    return False, "aceito (deveria ser recusado)"


def _valor(conn: psycopg.Connection, comando: str) -> object:
    with conn.transaction(force_rollback=True):
        linha = conn.execute(comando).fetchone()
    return None if linha is None else linha[0]


def _tentar(papel: str, tentativa: str, esperado: str, acao: Callable[[], tuple[bool, str]]):
    try:
        ok, obtido = acao()
    except psycopg.Error as erro:  # o erro vira resultado; o texto do servidor não é repetido
        ok, obtido = False, type(erro).__name__
    return Conferencia(papel, tentativa, esperado, ok, obtido)


def _igual(conn: psycopg.Connection, comando: str, esperado: object) -> tuple[bool, str]:
    valor = _valor(conn, comando)
    return valor == esperado, str(valor)


def _conferir_agente(cfg: Config) -> list[Conferencia]:
    from srag_agente.banco import conectar

    resultado = []
    with conectar(cfg, "agente", autocommit=True) as conn:
        ag = "agente"
        resultado += [
            _tentar(ag, "SELECT count(*) FROM gold.referencia", "1",
                    lambda: _igual(conn, "SELECT count(*) FROM gold.referencia", 1)),
            _tentar(ag, "INSERT INTO gold.referencia (sessão padrão)", "recusado",
                    lambda: _recusa(conn, "INSERT INTO gold.referencia SELECT * FROM "
                                          "gold.referencia WHERE false", _SOMENTE_LEITURA)),
            _tentar(ag, "SELECT em auditoria.execucoes", "InsufficientPrivilege",
                    lambda: _recusa(conn, "SELECT 1 FROM auditoria.execucoes LIMIT 1",
                                    _PRIVILEGIO)),
            _tentar(ag, "SHOW statement_timeout", "5s",
                    lambda: _igual(conn, "SHOW statement_timeout", "5s")),
            _tentar(ag, "SHOW transaction_read_only", "on",
                    lambda: _igual(conn, "SHOW transaction_read_only", "on")),
        ]  # fmt: skip
    # Sessão que desliga o somente leitura (o que um invasor tentaria primeiro): o que segura a
    # escrita agora é só o privilégio, que é o que se quer provar.
    with conectar(cfg, "agente", autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = off")
        ag = "agente (somente leitura desligado)"
        resultado += [
            _tentar(ag, "INSERT INTO gold.referencia", "InsufficientPrivilege",
                    lambda: _recusa(conn, "INSERT INTO gold.referencia SELECT * FROM "
                                          "gold.referencia WHERE false", _PRIVILEGIO)),
            _tentar(ag, "CREATE TEMP TABLE", "InsufficientPrivilege",
                    lambda: _recusa(conn, "CREATE TEMP TABLE t (x int)", _PRIVILEGIO)),
            _tentar(ag, "CREATE TABLE public.t", "InsufficientPrivilege",
                    lambda: _recusa(conn, "CREATE TABLE public.t (x int)", _PRIVILEGIO)),
            _tentar(ag, "CREATE TABLE gold.t", "InsufficientPrivilege",
                    lambda: _recusa(conn, "CREATE TABLE gold.t (x int)", _PRIVILEGIO)),
        ]  # fmt: skip
    return resultado


def _conferir_auditor(cfg: Config) -> list[Conferencia]:
    from srag_agente.banco import conectar

    au = "auditor"
    with conectar(cfg, "auditor", autocommit=True) as conn:
        return [
            _tentar(au, "SELECT count(*) FROM auditoria.limites_demo", "1",
                    lambda: _igual(conn, "SELECT count(*) FROM auditoria.limites_demo", 1)),
            _tentar(au, "DELETE FROM auditoria.execucoes", "InsufficientPrivilege",
                    lambda: _recusa(conn, "DELETE FROM auditoria.execucoes WHERE false",
                                    _PRIVILEGIO)),
            _tentar(au, "UPDATE auditoria.execucoes SET modelo", "InsufficientPrivilege",
                    lambda: _recusa(conn, "UPDATE auditoria.execucoes SET modelo = modelo "
                                          "WHERE false", _PRIVILEGIO)),
            # A execução nasce com o início do relógio do servidor: o auditor não escolhe a data
            # (senão datava a execução de ontem e passava pelo teto de hoje).
            _tentar(au, "INSERT com inicio em auditoria.execucoes", "InsufficientPrivilege",
                    lambda: _recusa(conn, "INSERT INTO auditoria.execucoes (id_execucao, inicio, "
                                          "provedor, modelo, parametros, versao_codigo, "
                                          "hash_prompts) SELECT gen_random_uuid(), now(), 'p', "
                                          "'m', '{}', 'v', 'h' WHERE false", _PRIVILEGIO)),
            _tentar(au, "UPDATE auditoria.limites_demo", "InsufficientPrivilege",
                    lambda: _recusa(conn, "UPDATE auditoria.limites_demo SET relatorios_dia = "
                                          "relatorios_dia WHERE false", _PRIVILEGIO)),
            _tentar(au, "DISABLE TRIGGER do teto", "InsufficientPrivilege",
                    lambda: _recusa(conn, f"ALTER TABLE auditoria.execucoes DISABLE TRIGGER "
                                          f"{GATILHO_TETO}", _PRIVILEGIO)),
            _tentar(au, "SELECT em gold.referencia", "InsufficientPrivilege",
                    lambda: _recusa(conn, "SELECT 1 FROM gold.referencia", _PRIVILEGIO)),
            _tentar(au, "CREATE TEMP TABLE", "InsufficientPrivilege",
                    lambda: _recusa(conn, "CREATE TEMP TABLE t (x int)", _PRIVILEGIO)),
        ]  # fmt: skip


def conferir_catalogo(conn: psycopg.Connection, *, incluir_extensoes: bool = True,
                      ) -> list[Conferencia]:  # fmt: skip
    """Consultas de catálogo (legíveis por qualquer papel): sem raw/silver, nada fora da demo,
    papéis sem poder de administrador nem participação em outro papel, configuração de sessão
    dos papéis igual à esperada, gatilho do teto ativo e (se pedido) só as extensões esperadas.
    """
    ca = "catalogo"

    def vazio(comando: str) -> tuple[bool, str]:
        # Só nome e tipo (catálogo), nunca conteúdo: o dono decide remover ou registrar.
        linhas = conn.execute(comando).fetchall()
        nomes = [f"{r[0]}.{r[1]} ({TIPOS_DE_OBJETO.get(r[2], r[2])})" if len(r) > 2 else str(r[0])
                 for r in linhas[:20]]  # fmt: skip
        resto = f" e mais {len(linhas) - 20}" if len(linhas) > 20 else ""
        return not linhas, "vazio" if not linhas else f"{len(linhas)} objeto(s): " + ", ".join(
            nomes) + resto  # fmt: skip

    def poderes() -> tuple[bool, str]:
        linhas = conn.execute(SQL_PODERES).fetchall()
        nomes = sorted(r[0] for r in linhas)
        ruins = [r[0] for r in linhas if any(r[1:6]) or r[6]]
        ok = nomes == ["srag_agente", "srag_auditor"] and not ruins
        return ok, "tudo false, sem participação" if ok else f"papéis {nomes}; com poder: {ruins}"

    def objetos() -> tuple[bool, str]:
        achados: dict[str, set[str]] = {"gold": set(), "auditoria": set()}
        tipos_ruins = []
        for esquema, nome, tipo in conn.execute(SQL_OBJETOS_DA_DEMO):
            achados[esquema].add(nome)
            if tipo != "r":
                tipos_ruins.append(f"{esquema}.{nome}")
        diferencas = [
            f"{esquema}: a mais {sorted(achados[esquema] - esperados)}, "
            f"faltam {sorted(esperados - achados[esquema])}"
            for esquema, esperados in OBJETOS_ESPERADOS.items()
            if achados[esquema] != esperados
        ]
        ok = not diferencas and not tipos_ruins
        return ok, "exatamente a lista" if ok else "; ".join(diferencas + tipos_ruins)

    def gatilho() -> tuple[bool, str]:
        linha = conn.execute(SQL_GATILHO, (GATILHO_TETO,)).fetchone()
        if linha is None:
            return False, "ausente"
        return (True, "ativo") if linha[0] == "O" else (False, "desligado")

    def configuracoes() -> tuple[bool, str]:
        # O próprio papel consegue `ALTER ROLE <si> SET ...`, e a mudança persiste. Só a chave
        # aparece na mensagem, nunca o valor.
        achadas = {(papel, chave, valor, todos) for papel, todos, item in
                   conn.execute(SQL_CONFIGURACOES)
                   for chave, _, valor in [item.partition("=")]}  # fmt: skip
        diferencas = [
            f"{p}: {c}{'' if t else ' (neste banco)'} a mais"
            for p, c, v, t in sorted(achadas - CONFIGURACOES_ESPERADAS)
        ]
        diferencas += [f"{p}: falta {c}" for p, c, v, t in
                       sorted(CONFIGURACOES_ESPERADAS - achadas)]  # fmt: skip
        return not diferencas, "como esperado" if not diferencas else "; ".join(diferencas)

    def extensoes() -> tuple[bool, str]:
        nomes = [n for (n,) in conn.execute(SQL_EXTENSOES)]
        return not nomes, ", ".join(nomes) if nomes else "nenhuma"

    with conn.transaction(force_rollback=True):
        return [
            _tentar(ca, "objetos fora de gold/auditoria", "vazio",
                    lambda: vazio(SQL_FORA_DA_DEMO)),
            _tentar(ca, "esquemas raw/silver", "vazio", lambda: vazio(SQL_RAW_SILVER)),
            _tentar(ca, "poderes de srag_agente e srag_auditor", "tudo false", poderes),
            _tentar(ca, "objetos de gold e auditoria", "exatamente a lista, só tabelas", objetos),
            _tentar(ca, f"gatilho {GATILHO_TETO}", "ativo", gatilho),
            _tentar(ca, "configurações de srag_agente e srag_auditor",
                    "agente: só somente leitura e 5s; auditor: nenhuma", configuracoes),
        ] + ([_tentar(ca, "extensões fora de plpgsql/vector", "nenhuma", extensoes)]
             if incluir_extensoes else [])  # fmt: skip


def conferir_demo(cfg: Config) -> list[Conferencia]:
    """Tabela da §2.4 do desenho: tentativas com agente e auditor e as consultas de catálogo."""
    from srag_agente.banco import conectar

    resultado = _conferir_agente(cfg) + _conferir_auditor(cfg)
    with conectar(cfg, "agente", autocommit=True) as conn:
        resultado += conferir_catalogo(conn)
    return resultado
