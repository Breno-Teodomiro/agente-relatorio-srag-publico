"""Pacote da demonstração: a gold local em CSV + `manifesto.json`, conferido na saída e na entrada.

- A exportação lê com o papel do agente: por GRANT, não alcança raw, silver nem auditoria.
- Só saem as colunas da lista fechada (`TABELAS_DEMO`), e cada valor tem de caber num domínio
  conhecido (UF, categoria, contagem, data, vetor). Valor fora do domínio reprova o pacote; a
  mensagem diz arquivo, linha e coluna, nunca o valor (que poderia ser o dado que escapou).
- O texto do dicionário (documento público) é o único texto livre: dado pessoal nele vira aviso.
- Nada do pacote é executado: a carga usa COPY com a lista de colunas do código, e os bytes que
  ela envia são os mesmos que passaram pela conferência (`ler_pacote`).
"""

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import IsolationLevel, sql

from srag_agente.config import Config
from srag_agente.demo import COLUNAS_PROIBIDAS, TABELAS_DEMO
from srag_agente.guardrails.padroes import mascarar_pii
from srag_agente.rag.embeddings import DIMENSAO, MODELO_EMBEDDING

FORMATO = 1
ARQUIVO_MANIFESTO = "manifesto.json"
TAMANHO_MAXIMO_MANIFESTO = 1_000_000
TAMANHO_MAXIMO_CSV = 50_000_000  # o pacote real é muito menor (`du -sh demo/pacote` mede)
PROBLEMAS_POR_COLUNA = 5
INTEIRO_MAXIMO = 2**31 - 1  # coluna int do Postgres
# A exportação lê tudo num retrato só; o padrão do papel (5 s por consulta) é curto para o COPY.
_SQL_SESSAO = (
    "SET LOCAL statement_timeout = '60s'",
    "SET LOCAL DateStyle = 'ISO, YMD'",  # data sempre AAAA-MM-DD, qualquer que seja o servidor
    "SET LOCAL client_encoding = 'UTF8'",
)

UFS_PACOTE = frozenset(
    {"AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB",
     "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO", "BR", "ND"}
)  # fmt: skip
# Valores que o 01_silver.sql produz para cada dimensão de gold.distribuicao.
CATEGORIAS: dict[str, frozenset[str]] = {
    "classificacao": frozenset({"em_investigacao", "influenza", "outro_virus", "outro_agente",
                                "nao_especificado", "covid19", "ignorado"}),
    "faixa_etaria": frozenset({"ignorado", "<1", "1-4", "5-11", "12-17", "18-39", "40-59",
                               "60+"}),
}  # fmt: skip

# Tipo de cada coluna permitida. Coluna sem tipo aqui não passa (teste amarra à TABELAS_DEMO).
_DATAS_OPCIONAIS = ("data_referencia", "data_consolidada", "inicio_base", "periodo_inicio",
                    "periodo_fim")  # fmt: skip
_CONTAGENS = ("id", "pagina", "casos", "total_casos", "obitos", "com_desfecho", "uti_sim",
              "uti_informada", "vacina_covid_sim", "vacina_covid_informada", "vacina_gripe_sim",
              "vacina_gripe_informada", "casos_7d_atual", "casos_7d_anterior", "casos_30d_atual",
              "casos_30d_anterior", "casos_em_consolidacao")  # fmt: skip
TIPOS: dict[str, str] = {
    **dict.fromkeys(_DATAS_OPCIONAIS, "data_ou_nula"),
    **dict.fromkeys(_CONTAGENS, "contagem"),
    "uf": "uf",
    "dia": "data",
    "dimensao": "dimensao",
    "categoria": "categoria",
    "campo": "campo",
    "texto": "texto_publico",
    "embedding": "vetor",
}
_DESCRICAO = {
    "uf": "UF, BR ou ND",
    "dimensao": "dimensão conhecida",
    "categoria": "categoria da dimensão",
    "contagem": "inteiro >= 0",
    "data": "data AAAA-MM-DD",
    "data_ou_nula": "data AAAA-MM-DD ou vazio",
    "campo": "nome de campo em maiúsculas ou vazio",
    "texto_publico": "texto não vazio",
    "vetor": f"vetor de {DIMENSAO} números finitos",
}

_INTEIRO = re.compile(r"\d{1,10}")
_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
_CAMPO = re.compile(r"[A-Z][A-Z0-9_]{0,39}")
_NUMERO = re.compile(r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_NOME_COLUNA = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_VERSAO = re.compile(r"[0-9A-Za-z._-]{1,64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CHAVES_MANIFESTO = {"formato", "gerado_em", "versao_codigo", "modelo_embedding", "dimensao",
                     "tabelas"}  # fmt: skip
_CHAVES_TABELA = {"arquivo", "colunas", "linhas", "sha256"}


class PacoteInvalido(ValueError):  # noqa: N818 (é recusa, não defeito do programa)
    """O pacote (ou o destino dele) não passou na conferência. `problemas` lista cada motivo."""

    def __init__(self, problemas: list[str]) -> None:
        self.problemas = list(problemas)
        super().__init__("pacote recusado:\n" + "\n".join(f"  - {p}" for p in self.problemas))


@dataclass(frozen=True)
class TabelaPacote:
    arquivo: str
    colunas: tuple[str, ...]
    linhas: int
    sha256: str


@dataclass(frozen=True)
class Manifesto:
    formato: int
    gerado_em: datetime
    versao_codigo: str
    modelo_embedding: str
    dimensao: int
    tabelas: dict[str, TabelaPacote]
    sha256: str  # do manifesto.json gravado: é a identidade do pacote


@dataclass(frozen=True)
class PacoteLido:
    """Manifesto + o conteúdo exato de cada CSV que passou na conferência (o que a carga envia)."""

    manifesto: Manifesto
    conteudos: dict[str, bytes]


@dataclass
class _Analise:
    cabecalho: list[str]
    linhas: list[list[str]]
    problemas: list[str]
    avisos: list[str]


# --- regras de nome e de valor -----------------------------------------------------------------
def coluna_proibida(nome: str) -> bool:
    """Nome que nunca entra no pacote (ADR 0004), com curinga; não diferencia maiúscula."""
    return any(fnmatchcase(nome.lower(), padrao) for padrao in COLUNAS_PROIBIDAS)


def sha256_arquivo(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def _data_ok(valor: str) -> bool:
    if not _DATA.fullmatch(valor):
        return False
    try:
        date.fromisoformat(valor)
    except ValueError:
        return False
    return True


def _vetor_ok(valor: str) -> bool:
    if not (valor.startswith("[") and valor.endswith("]")):
        return False
    numeros = valor[1:-1].split(",")
    return len(numeros) == DIMENSAO and all(
        _NUMERO.fullmatch(n) and math.isfinite(float(n)) for n in numeros
    )


def _valor_ok(tipo: str, valor: str, linha: dict[str, str]) -> bool:
    match tipo:
        case "uf":
            return valor in UFS_PACOTE
        case "dimensao":
            return valor in CATEGORIAS
        case "categoria":
            return valor in CATEGORIAS.get(linha.get("dimensao", ""), frozenset())
        case "contagem":
            return bool(_INTEIRO.fullmatch(valor)) and int(valor) <= INTEIRO_MAXIMO
        case "data":
            return _data_ok(valor)
        case "data_ou_nula":
            return valor == "" or _data_ok(valor)
        case "campo":
            return valor == "" or bool(_CAMPO.fullmatch(valor))
        case "texto_publico":
            return valor.strip() != ""
        case "vetor":
            return _vetor_ok(valor)
    return False


def _nome_exibivel(nome: str, posicao: int) -> str:
    # Um "cabeçalho" que é na verdade uma linha de dados não vai para a mensagem.
    return nome if _NOME_COLUNA.fullmatch(nome.lower()) else f"(sem nome válido, posição {posicao})"


# --- leitura e análise de um CSV ----------------------------------------------------------------
def _analisar(tabela: str, dados: bytes) -> _Analise:
    arquivo = f"{tabela}.csv"
    permitidas = TABELAS_DEMO[tabela][0]
    try:
        registros = list(csv.reader(io.StringIO(dados.decode("utf-8"), newline="")))
    except (UnicodeDecodeError, csv.Error):
        return _Analise([], [], [f"{arquivo}: não é CSV em UTF-8 legível"], [])
    if not registros:
        return _Analise([], [], [f"{arquivo}: vazio (nem cabeçalho)"], [])
    cabecalho, linhas = registros[0], registros[1:]
    problemas: list[str] = []
    for posicao, nome in enumerate(cabecalho, start=1):
        exibivel = _nome_exibivel(nome, posicao)
        if coluna_proibida(nome):
            problemas.append(f"{arquivo}: coluna {exibivel} é proibida (ADR 0004)")
        if nome not in permitidas:
            problemas.append(f"{arquivo}: coluna {exibivel} fora da lista da demo")
    problemas += _problemas_de_valor(tabela, cabecalho, linhas)
    return _Analise(cabecalho, linhas, problemas, _avisos_de_texto(arquivo, cabecalho, linhas))


def _problemas_de_valor(tabela: str, cabecalho: list[str], linhas: list[list[str]]) -> list[str]:
    arquivo, permitidas = f"{tabela}.csv", TABELAS_DEMO[tabela][0]
    problemas: list[str] = []
    contagem: Counter[str] = Counter()

    def anotar(chave: str, mensagem: str) -> None:
        contagem[chave] += 1
        if contagem[chave] <= PROBLEMAS_POR_COLUNA:
            problemas.append(mensagem)

    for indice, valores in enumerate(linhas):
        numero = indice + 2  # linha 1 é o cabeçalho
        if len(valores) != len(cabecalho):
            anotar("", f"{arquivo} linha {numero}: {len(valores)} campos, esperado "
                       f"{len(cabecalho)}")  # fmt: skip
            continue
        linha = dict(zip(cabecalho, valores, strict=True))
        for coluna, valor in linha.items():
            tipo = TIPOS.get(coluna)
            if tipo is None or coluna not in permitidas:
                continue  # coluna fora da lista já reprovou pelo nome
            if not _valor_ok(tipo, valor, linha):
                anotar(coluna, f"{arquivo} linha {numero}, coluna {coluna}: valor fora do "
                               f"domínio ({_DESCRICAO[tipo]})")  # fmt: skip
    for chave, total in contagem.items():
        if total > PROBLEMAS_POR_COLUNA:
            onde = f"coluna {chave}" if chave else "número de campos"
            problemas.append(f"{arquivo}, {onde}: e mais {total - PROBLEMAS_POR_COLUNA} "
                             "linha(s) com o mesmo problema")  # fmt: skip
    return problemas


def _avisos_de_texto(arquivo: str, cabecalho: list[str], linhas: list[list[str]]) -> list[str]:
    avisos = []
    colunas = [i for i, nome in enumerate(cabecalho) if TIPOS.get(nome) == "texto_publico"]
    for indice, valores in enumerate(linhas):
        for i in colunas:
            if i < len(valores) and (tipos := mascarar_pii(valores[i])[1]):
                avisos.append(
                    f"{arquivo} linha {indice + 2}: possível dado pessoal no texto público "
                    f"({', '.join(sorted(set(tipos)))}); confira antes de publicar"
                )
    return avisos


def _ler_arquivo(caminho: Path, limite: int) -> tuple[bytes | None, str | None]:
    """(conteúdo, problema). Link simbólico, ausente ou grande demais não são lidos."""
    if caminho.is_symlink():
        return None, f"{caminho.name}: é link simbólico; o pacote só aceita arquivo comum"
    if not caminho.is_file():
        return None, f"{caminho.name}: não encontrado no pacote"
    if caminho.stat().st_size > limite:
        return None, f"{caminho.name}: maior que {limite} bytes"
    return caminho.read_bytes(), None


def _arquivos_inesperados(pasta: Path) -> list[str]:
    esperados = {ARQUIVO_MANIFESTO, *(f"{t}.csv" for t in TABELAS_DEMO)}
    return sorted(p.name for p in pasta.iterdir() if p.name not in esperados)


# --- conferências públicas ---------------------------------------------------------------------
def conferir_colunas_gold(colunas: dict[str, list[str]]) -> list[str]:
    """Compara as colunas da gold com a lista da demo. Coluna nova não sai sem decisão."""
    problemas = []
    for tabela, (permitidas, _) in TABELAS_DEMO.items():
        if tabela not in colunas:
            problemas.append(f"gold.{tabela} não encontrada (rode `make carga` e `make indexar`)")
            continue
        for coluna in colunas[tabela]:
            if coluna not in permitidas:
                extra = " e é proibida (ADR 0004)" if coluna_proibida(coluna) else ""
                problemas.append(f"coluna {coluna} de gold.{tabela} não está na lista da demo"
                                 f"{extra}; revise antes de publicar")  # fmt: skip
        problemas += [f"coluna {c} da lista não existe em gold.{tabela}"
                      for c in permitidas if c not in colunas[tabela]]  # fmt: skip
    return problemas


def conferir_sem_pii(pasta: Path) -> list[str]:
    """Problemas que impedem publicar (vazio = ok): cabeçalho fora da lista ou proibido, valor
    fora do domínio, arquivo inesperado. Roda no fim da exportação e no começo da carga."""
    pasta = Path(pasta)
    problemas = [f"arquivo inesperado no pacote: {nome}" for nome in _arquivos_inesperados(pasta)]
    for tabela in TABELAS_DEMO:
        dados, problema = _ler_arquivo(pasta / f"{tabela}.csv", TAMANHO_MAXIMO_CSV)
        problemas += [problema] if problema else _analisar(tabela, dados).problemas
    return problemas


def avisos_pii(pasta: Path) -> list[str]:
    """Possível dado pessoal no texto público do dicionário: aviso para quem publica decidir."""
    avisos = []
    for tabela in TABELAS_DEMO:
        dados, problema = _ler_arquivo(Path(pasta) / f"{tabela}.csv", TAMANHO_MAXIMO_CSV)
        if not problema:
            avisos += _analisar(tabela, dados).avisos
    return avisos


def ler_manifesto(pasta: Path) -> Manifesto:
    """Lê e valida a forma do manifesto (tipos, nomes de arquivo, versão). Não lê os CSV."""
    caminho = Path(pasta) / ARQUIVO_MANIFESTO
    bruto, problema = _ler_arquivo(caminho, TAMANHO_MAXIMO_MANIFESTO)
    if problema:
        raise PacoteInvalido([f"manifesto: {problema}"])
    try:
        dados = json.loads(bruto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PacoteInvalido(["manifesto: não é JSON em UTF-8 legível"]) from None
    problemas = _problemas_do_manifesto(dados)
    if problemas:
        raise PacoteInvalido(problemas)
    tabelas = {
        nome: TabelaPacote(t["arquivo"], tuple(t["colunas"]), t["linhas"], t["sha256"])
        for nome, t in dados["tabelas"].items()
    }
    return Manifesto(
        formato=dados["formato"],
        gerado_em=datetime.fromisoformat(dados["gerado_em"]),
        versao_codigo=dados["versao_codigo"],
        modelo_embedding=dados["modelo_embedding"],
        dimensao=dados["dimensao"],
        tabelas=tabelas,
        sha256=hashlib.sha256(bruto).hexdigest(),
    )


def _inteiro(valor: object) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _gerado_em_ok(valor: object) -> bool:
    try:
        return isinstance(valor, str) and datetime.fromisoformat(valor).tzinfo is not None
    except ValueError:
        return False


def _problemas_do_manifesto(dados: object) -> list[str]:
    if not isinstance(dados, dict) or set(dados) != _CHAVES_MANIFESTO:
        return ["manifesto: chaves diferentes das do formato"]
    problemas = []
    if not _inteiro(dados["formato"]) or dados["formato"] != FORMATO:
        problemas.append(f"manifesto: formato diferente de {FORMATO}")
    if not _gerado_em_ok(dados["gerado_em"]):
        problemas.append("manifesto: gerado_em não é data e hora com fuso")
    if not (isinstance(dados["versao_codigo"], str) and _VERSAO.fullmatch(dados["versao_codigo"])):
        problemas.append("manifesto: versao_codigo fora do formato")
    if dados["modelo_embedding"] != MODELO_EMBEDDING:
        problemas.append("manifesto: modelo de embeddings diferente do que o código usa")
    if not _inteiro(dados["dimensao"]) or dados["dimensao"] != DIMENSAO:
        problemas.append(f"manifesto: dimensão diferente de {DIMENSAO}")
    tabelas = dados["tabelas"]
    if not isinstance(tabelas, dict) or set(tabelas) != set(TABELAS_DEMO):
        return [*problemas, "manifesto: tabelas diferentes da lista da demo"]
    for nome, tabela in tabelas.items():
        if not isinstance(tabela, dict) or set(tabela) != _CHAVES_TABELA:
            problemas.append(f"manifesto: entrada de {nome} fora do formato")
            continue
        if tabela["arquivo"] != f"{nome}.csv":
            problemas.append(f"manifesto: arquivo de {nome} tem de ser {nome}.csv")
        if not (isinstance(tabela["colunas"], list)
                and all(isinstance(c, str) for c in tabela["colunas"])):  # fmt: skip
            problemas.append(f"manifesto: colunas de {nome} fora do formato")
        if not _inteiro(tabela["linhas"]) or tabela["linhas"] < 0:
            problemas.append(f"manifesto: linhas de {nome} fora do formato")
        if not (isinstance(tabela["sha256"], str) and _SHA256.fullmatch(tabela["sha256"])):
            problemas.append(f"manifesto: sha256 de {nome} fora do formato")
    return problemas


def ler_pacote(pasta: Path) -> PacoteLido:
    """Conferência completa, com os bytes lidos uma vez só: o que a carga envia é o que passou."""
    pasta = Path(pasta)
    manifesto = ler_manifesto(pasta)
    problemas = [f"arquivo inesperado no pacote: {nome}" for nome in _arquivos_inesperados(pasta)]
    conteudos: dict[str, bytes] = {}
    for tabela, (permitidas, _) in TABELAS_DEMO.items():
        dados, problema = _ler_arquivo(pasta / f"{tabela}.csv", TAMANHO_MAXIMO_CSV)
        if problema:
            problemas.append(problema)
            continue
        conteudos[tabela] = dados
        esperado = manifesto.tabelas[tabela]
        if hashlib.sha256(dados).hexdigest() != esperado.sha256:
            problemas.append(f"{tabela}.csv: sha256 não confere com o manifesto")
        if esperado.colunas != permitidas:
            problemas.append(f"manifesto: colunas de {tabela} diferentes da lista da demo")
        analise = _analisar(tabela, dados)
        problemas += analise.problemas
        if analise.cabecalho and tuple(analise.cabecalho) != permitidas:
            problemas.append(f"{tabela}.csv: colunas diferentes da lista da demo (nome ou ordem)")
        if len(analise.linhas) != esperado.linhas:
            problemas.append(f"{tabela}.csv: número de linhas não confere com o manifesto")
        if tabela == "referencia":
            problemas += _problemas_da_referencia(analise)
    if problemas:
        raise PacoteInvalido(problemas)
    return PacoteLido(manifesto, conteudos)


def _problemas_da_referencia(analise: _Analise) -> list[str]:
    # A referência é uma linha só (agregado sem GROUP BY); base vazia não vira demonstração.
    if len(analise.linhas) != 1:
        return ["referencia.csv: precisa ter exatamente uma linha"]
    linha = dict(zip(analise.cabecalho, analise.linhas[0], strict=False))
    if linha.get("total_casos") in (None, "", "0"):
        return ["referencia.csv: gold vazia (total_casos = 0); rode `make carga` antes"]
    return []


def conferir_pacote(pasta: Path) -> Manifesto:
    """sha256, colunas, linhas e domínios de todo o pacote; levanta PacoteInvalido."""
    return ler_pacote(pasta).manifesto


# --- montagem e gravação ------------------------------------------------------------------------
def montar_manifesto(pasta: Path, *, gerado_em: datetime, versao_codigo: str) -> Manifesto:
    """Calcula linhas e sha256 dos CSV da pasta e grava o manifesto (JSON determinístico)."""
    pasta = Path(pasta)
    if not _VERSAO.fullmatch(versao_codigo):
        raise PacoteInvalido(["versão do código fora do formato (SRAG_VERSAO_CODIGO?)"])
    if gerado_em.tzinfo is None:
        raise ValueError("gerado_em precisa de fuso")
    tabelas = {}
    for tabela in TABELAS_DEMO:
        dados = (pasta / f"{tabela}.csv").read_bytes()
        analise = _analisar(tabela, dados)
        tabelas[tabela] = {
            "arquivo": f"{tabela}.csv",
            "colunas": analise.cabecalho,
            "linhas": len(analise.linhas),
            "sha256": hashlib.sha256(dados).hexdigest(),
        }
    conteudo = {
        "formato": FORMATO,
        "gerado_em": gerado_em.isoformat(),
        "versao_codigo": versao_codigo,
        "modelo_embedding": MODELO_EMBEDDING,
        "dimensao": DIMENSAO,
        "tabelas": tabelas,
    }
    texto = json.dumps(conteudo, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (pasta / ARQUIVO_MANIFESTO).write_text(texto, encoding="utf-8")
    return ler_manifesto(pasta)


def _parece_pacote(pasta: Path) -> bool:
    """Só substituímos pasta que contém apenas arquivos de pacote (ou nada)."""
    esperados = {ARQUIVO_MANIFESTO, *(f"{t}.csv" for t in TABELAS_DEMO)}
    return all(
        filho.name in esperados and filho.is_file() and not filho.is_symlink()
        for filho in pasta.iterdir()
    )


def publicar_pasta(nova: Path, destino: Path) -> None:
    """Troca `destino` por `nova` com renomeações; nunca apaga pasta que não seja pacote."""
    nova, destino = Path(nova), Path(destino)
    if destino.is_symlink() or (destino.exists() and not destino.is_dir()):
        raise PacoteInvalido([f"{destino} não é um pacote da demo; escolha outra pasta"])
    if not destino.exists():
        os.replace(nova, destino)
        return
    if not _parece_pacote(destino):
        raise PacoteInvalido([f"{destino} não é um pacote da demo (tem outros arquivos); "
                              "escolha outra pasta"])  # fmt: skip
    antiga = destino.with_name(f".{destino.name}-antigo-{uuid4().hex[:8]}")
    os.replace(destino, antiga)
    try:
        os.replace(nova, destino)
    except BaseException:
        os.replace(antiga, destino)
        raise
    shutil.rmtree(antiga)


def _colunas_gold(conn: psycopg.Connection) -> dict[str, list[str]]:
    colunas: dict[str, list[str]] = {}
    for tabela, coluna in conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'gold' AND table_name = ANY(%s) "
        "ORDER BY table_name, ordinal_position",
        (list(TABELAS_DEMO),),
    ):
        colunas.setdefault(tabela, []).append(coluna)
    return colunas


def _ordem(ordem: str) -> sql.Composable:
    # "uf, dia" → identificadores; "1" → posição. Texto vem do código (TABELAS_DEMO), nunca de fora.
    itens = [i.strip() for i in ordem.split(",")]
    return sql.SQL(", ").join(sql.SQL(i) if i.isdigit() else sql.Identifier(i) for i in itens)


def _copiar_tabela(conn: psycopg.Connection, tabela: str, destino: Path) -> None:
    colunas, ordem = TABELAS_DEMO[tabela]
    comando = sql.SQL(
        "COPY (SELECT {colunas} FROM {tabela} ORDER BY {ordem}) TO STDOUT WITH (FORMAT csv, "
        "HEADER true)"
    ).format(
        colunas=sql.SQL(", ").join(map(sql.Identifier, colunas)),
        tabela=sql.Identifier("gold", tabela),
        ordem=_ordem(ordem),
    )
    with destino.open("wb") as arquivo, conn.cursor() as cursor, cursor.copy(comando) as copia:
        for bloco in copia:
            arquivo.write(bloco)


def exportar(cfg: Config, saida: Path, *, agora: datetime | None = None) -> Manifesto:
    """Gold local → pacote em `saida`, com o papel do agente e num retrato só (REPEATABLE READ).

    Grava numa pasta provisória ao lado e só troca a pasta final se tudo passou.
    """
    from srag_agente.auditoria.versao import versao_codigo
    from srag_agente.banco import conectar

    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    if saida.exists() and (saida.is_symlink() or not saida.is_dir() or not _parece_pacote(saida)):
        raise PacoteInvalido([f"{saida} não é um pacote da demo; escolha outra pasta"])
    provisoria = Path(tempfile.mkdtemp(dir=saida.parent, prefix=f".{saida.name}-novo-"))
    try:
        with conectar(cfg, "agente") as conn:
            conn.isolation_level = IsolationLevel.REPEATABLE_READ
            conn.read_only = True
            with conn.transaction():
                for comando in _SQL_SESSAO:
                    conn.execute(comando)
                if problemas := conferir_colunas_gold(_colunas_gold(conn)):
                    raise PacoteInvalido(problemas)
                for tabela in TABELAS_DEMO:
                    _copiar_tabela(conn, tabela, provisoria / f"{tabela}.csv")
        montar_manifesto(provisoria, gerado_em=agora or datetime.now(UTC),
                         versao_codigo=versao_codigo())  # fmt: skip
        ler_pacote(provisoria)  # a mesma conferência que a carga fará
        publicar_pasta(provisoria, saida)
    finally:
        shutil.rmtree(provisoria, ignore_errors=True)
    return ler_manifesto(saida)
