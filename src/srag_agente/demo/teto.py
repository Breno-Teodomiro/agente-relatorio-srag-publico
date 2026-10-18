"""Teto diário da demonstração: lê o uso do dia no banco e decide se a próxima execução cabe.

Fonte única: `auditoria.execucoes`/`eventos` e os limites em `auditoria.limites_demo`, todos no
banco da demonstração (valem para todos os visitantes e sobrevivem a reinício do app). O dia é o
de Brasília, calculado pelo relógio do banco. A tela confere aqui, dentro da trava global; entre
processos, quem segura o limite de relatórios é o gatilho do banco (SQLSTATE `SR429`).

Qualquer falha ao ler o uso sobe como exceção: quem chama não gera (falha fechada).
"""

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg

from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.guardrails.limites import Limites

# Nome do gatilho criado pela carga da demo (demo/sql/02_teto_demo.sql); um teste amarra os dois.
GATILHO_TETO = "teto_diario_de_execucoes"
# A leitura do uso roda com a trava global pega: não pode esperar o banco sem fim.
TEMPO_MAXIMO_LEITURA = "5s"

COMPLEMENTO_TETO = (
    "Renova à meia-noite (horário de Brasília). A aba Dicionário de dados continua disponível, "
    "e há um relatório de exemplo (relatorios/exemplo, no repositório)."
)
# Recusa do gatilho (corrida entre processos): o texto do Postgres nunca vai para a tela.
MENSAGEM_TETO_BANCO = f"A demonstração atingiu o limite diário de relatórios. {COMPLEMENTO_TETO}"

SQL_USO_HOJE = """
WITH limite AS (SELECT * FROM auditoria.limites_demo WHERE id = 1),
     dia AS (SELECT date_trunc('day', now() AT TIME ZONE l.fuso) AT TIME ZONE l.fuso AS desde
             FROM limite l)
SELECT
  (SELECT count(*) FROM auditoria.execucoes e WHERE e.inicio >= d.desde)::int AS relatorios,
  (SELECT count(*) FROM auditoria.execucoes e
     WHERE e.inicio >= d.desde AND e.status = 'em_andamento')::int AS abertas,
  (SELECT coalesce(sum(e.custo_usd), 0) FROM auditoria.execucoes e
     WHERE e.inicio >= d.desde)::float AS custo_usd,
  (SELECT count(*) FROM auditoria.eventos v
     WHERE v.momento >= d.desde AND v.tipo = 'ferramenta'
       AND v.ferramenta = 'buscar_noticias')::int AS buscas,
  l.relatorios_dia, l.buscas_dia, l.custo_usd_dia::float AS custo_usd_dia,
  d.desde + interval '1 day' AS renova_em
FROM limite l CROSS JOIN dia d
"""

# O gatilho tem de existir, ligado, na tabela de execuções (pg_trigger é legível por todos).
SQL_GATILHO = """
SELECT 1 FROM pg_trigger
WHERE tgrelid = to_regclass('auditoria.execucoes') AND tgname = %s
  AND NOT tgisinternal AND tgenabled <> 'D'
"""

SQL_PACOTE = """
SELECT sha256, versao_codigo, data_referencia FROM gold.pacote_demo
ORDER BY carregado_em DESC LIMIT 1
"""


class TetoIndisponivelError(RuntimeError):
    """O banco da demonstração não tem limites configurados: nada é gerado."""


@dataclass(frozen=True)
class UsoDiario:
    relatorios: int
    abertas: int
    custo_usd: float
    buscas: int
    relatorios_dia: int
    buscas_dia: int
    custo_usd_dia: float
    renova_em: datetime


@dataclass(frozen=True)
class PacoteDemo:
    sha256: str
    versao_codigo: str
    data_referencia: date | None


def custo_maximo_execucao(cfg: Config) -> float:
    """Pior caso de uma execução: todos os tokens do teto ao preço mais alto."""
    return cfg.max_tokens_execucao * max(cfg.preco_entrada_usd_mtok, cfg.preco_saida_usd_mtok) / 1e6


def _valor_estranho(*valores: float) -> bool:
    # numeric do Postgres aceita NaN e o CHECK (x > 0) deixa passar; NaN > x é falso no Python.
    return any(not math.isfinite(v) or v < 0 for v in valores)


def motivo_teto(uso: UsoDiario, reserva_usd: float, buscas_por_execucao: int) -> str | None:
    """Mensagem para o visitante se a próxima execução passaria de algum teto; None se pode."""
    if uso.relatorios >= uso.relatorios_dia:
        return f"A demonstração atingiu o limite de {uso.relatorios_dia} relatórios de hoje."
    if uso.buscas + buscas_por_execucao > uso.buscas_dia:
        return "A demonstração atingiu o limite diário de buscas de notícias."
    if _valor_estranho(uso.custo_usd, uso.custo_usd_dia, reserva_usd):
        return "A demonstração não conseguiu conferir o gasto de hoje com o modelo de linguagem."
    if uso.custo_usd + (uso.abertas + 1) * reserva_usd > uso.custo_usd_dia:
        return "A demonstração atingiu o limite diário de gasto com o modelo de linguagem."
    return None


def ler_uso(conn: psycopg.Connection) -> UsoDiario:
    """Uso de hoje e limites; sem linha de limites, erro (falha fechada)."""
    with conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = '{TEMPO_MAXIMO_LEITURA}'")
        linha = conn.execute(SQL_USO_HOJE).fetchone()
    if linha is None:
        raise TetoIndisponivelError("demonstração sem limites configurados")
    return UsoDiario(*linha)


def uso_de_hoje(cfg: Config) -> UsoDiario:
    with conectar(cfg, "auditor", autocommit=True) as conn:
        return ler_uso(conn)


def motivo_para(cfg: Config, uso: UsoDiario) -> str | None:
    """`motivo_teto` com a reserva e o teto de buscas desta configuração."""
    return motivo_teto(uso, custo_maximo_execucao(cfg), Limites().max_buscas_noticias)


def conferir_teto(cfg: Config) -> str | None:
    """Abre a conexão do auditor, lê o uso e decide. Falha de leitura sobe (não gera)."""
    return motivo_para(cfg, uso_de_hoje(cfg))


def problemas_banco_demo(conn: psycopg.Connection) -> list[str]:
    """O que falta no banco para a demonstração abrir: gatilho do teto e linha de limites."""
    problemas = []
    if conn.execute(SQL_GATILHO, (GATILHO_TETO,)).fetchone() is None:
        problemas.append(f"gatilho {GATILHO_TETO} ausente ou desligado")
    try:
        ler_uso(conn)
    except TetoIndisponivelError:
        problemas.append("auditoria.limites_demo sem a linha de limites")
    except psycopg.errors.UndefinedTable:
        problemas.append("falta a tabela auditoria.limites_demo")
    return problemas


def prontidao_banco_demo(cfg: Config) -> list[str]:
    with conectar(cfg, "auditor", autocommit=True) as conn:
        return problemas_banco_demo(conn)


def ler_pacote(conn: psycopg.Connection) -> PacoteDemo | None:
    linha = conn.execute(SQL_PACOTE).fetchone()
    return PacoteDemo(*linha) if linha else None


def pacote_carregado(cfg: Config) -> PacoteDemo | None:
    """Último pacote carregado (proveniência do rodapé), lido com o papel do agente."""
    with conectar(cfg, "agente", autocommit=True) as conn:
        return ler_pacote(conn)


def _kb_do_status(texto: str, campo: str) -> int | None:
    for linha in texto.splitlines():
        if linha.startswith(f"{campo}:"):
            partes = linha.split()
            return int(partes[1]) if len(partes) >= 2 and partes[1].isdigit() else None
    return None


def _ler(caminho: Path) -> str | None:
    try:
        return caminho.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None


def memoria_kb(
    *,
    status: Path = Path("/proc/self/status"),
    cgroup_v2: Path = Path("/sys/fs/cgroup/memory.max"),
    cgroup_v1: Path = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
) -> dict[str, Any]:
    """RSS e pico do processo e limite do cgroup (v2 ou v1), em KB; None se não der para ler."""
    texto_status = _ler(status) or ""
    limite = None
    for caminho in (cgroup_v2, cgroup_v1):
        bruto = (_ler(caminho) or "").strip()
        if bruto.isdigit():
            limite = int(bruto) // 1024
            break
    return {
        "rss_kb": _kb_do_status(texto_status, "VmRSS"),
        "pico_kb": _kb_do_status(texto_status, "VmHWM"),
        "limite_kb": limite,
    }
