"""Carga da camada raw: o CSV exatamente como veio, todas as colunas como texto."""

import codecs
import re
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Literal

import psycopg
from psycopg import sql

Codificacao = Literal["UTF8", "LATIN1"]

# Nome da codificação no Postgres → nome no Python.
CODECS: dict[Codificacao, str] = {"UTF8": "utf-8", "LATIN1": "latin-1"}
TAMANHO_BLOCO = 1 << 20
_NOME_COLUNA = re.compile(r"^[a-z0-9_]+$")


@contextmanager
def abrir_csv(origem: Path) -> Iterator[BinaryIO]:
    """Abre o CSV direto do zip (sem extrair) ou um CSV solto, fechando tudo ao sair."""
    if origem.suffix.lower() != ".zip":
        with origem.open("rb") as fluxo:
            yield fluxo
        return
    with zipfile.ZipFile(origem) as arquivo_zip:
        nomes = [n for n in arquivo_zip.namelist() if n.lower().endswith(".csv")]
        if not nomes:
            raise ValueError(f"O zip {origem.name} não tem nenhum CSV.")
        if len(nomes) > 1:
            raise ValueError(f"O zip {origem.name} tem mais de um CSV: {nomes}")
        with arquivo_zip.open(nomes[0]) as fluxo:
            yield fluxo


def detectar_codificacao(origem: Path, tamanho_bloco: int = TAMANHO_BLOCO) -> Codificacao:
    """UTF-8 se o arquivo inteiro for UTF-8 válido; senão latin-1 (formato antigo do DATASUS).

    Lê o arquivo todo: um único byte inválido no fim já basta para não ser UTF-8.
    """
    decodificador = codecs.getincrementaldecoder("utf-8")()
    with abrir_csv(origem) as fluxo:
        try:
            while bloco := fluxo.read(tamanho_bloco):
                decodificador.decode(bloco)
            decodificador.decode(b"", final=True)
        except UnicodeDecodeError:
            return "LATIN1"
    return "UTF8"


def ler_cabecalho(fluxo: BinaryIO, codificacao: Codificacao = "UTF8") -> list[str]:
    """Lê a primeira linha e devolve os nomes das colunas em minúsculas, já validados.

    Os nomes viram identificadores SQL, então só passam letras, dígitos e `_`.
    """
    linha = fluxo.readline().decode(CODECS[codificacao]).strip()
    colunas = [coluna.strip().strip('"').lower() for coluna in linha.split(";")]
    invalidas = [c for c in colunas if not _NOME_COLUNA.match(c)]
    if invalidas:
        raise ValueError(f"Cabeçalho com nomes de coluna inesperados: {invalidas[:5]}")
    repetidas = sorted({c for c in colunas if colunas.count(c) > 1})
    if repetidas:
        raise ValueError(f"Cabeçalho com colunas repetidas: {repetidas[:5]}")
    return colunas


def carregar_raw(conn: psycopg.Connection, origem: Path) -> int:
    """Recria `raw.srag` e copia o CSV por streaming. Devolve o total de linhas carregadas.

    Tudo num bloco `conn.transaction()`: se a cópia falhar, a `raw.srag` anterior continua
    como estava, com a conexão em autocommit ou não. Se o chamador já tiver uma transação
    aberta, o bloco vira savepoint e a confirmação final fica com ele.
    """
    codificacao = detectar_codificacao(origem)
    with conn.transaction(), abrir_csv(origem) as fluxo:
        colunas = ler_cabecalho(fluxo, codificacao)
        identificadores = sql.SQL(", ").join(sql.Identifier(c) for c in colunas)
        definicoes = sql.SQL(", ").join(
            sql.SQL("{} text").format(sql.Identifier(c)) for c in colunas
        )
        conn.execute("DROP TABLE IF EXISTS raw.srag CASCADE")
        conn.execute(sql.SQL("CREATE TABLE raw.srag ({})").format(definicoes))
        copia = sql.SQL(
            "COPY raw.srag ({}) FROM STDIN WITH (FORMAT csv, DELIMITER ';', ENCODING {})"
        ).format(identificadores, sql.Literal(codificacao))
        with conn.cursor() as cursor, cursor.copy(copia) as destino:
            while bloco := fluxo.read(TAMANHO_BLOCO):
                destino.write(bloco)
        return conn.execute("SELECT count(*) FROM raw.srag").fetchone()[0]
