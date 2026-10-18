"""Pacote da demonstração: lista fechada de colunas, domínios, manifesto e sha256. Sem banco."""

import csv
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from srag_agente.config import RAIZ_PROJETO
from srag_agente.demo import COLUNAS_PROIBIDAS, TABELAS_DEMO, pacote
from srag_agente.demo.pacote import PacoteInvalido

GERADO_EM = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
VETOR = "[" + ",".join(["0.125"] * 384) + "]"

# fmt: off
LINHAS_VALIDAS: dict[str, list[list[str]]] = {
    "referencia": [["2025-06-25", "2025-06-11", "2025-01-01", "120"]],
    "casos_diarios": [["BR", "2025-06-24", "7"], ["SP", "2025-06-24", "3"],
                      ["ND", "2025-06-01", "1"]],
    "indicadores": [["BR", "120", "9", "80", "20", "100", "30", "90", "25", "88", "2025-01-01",
                     "2025-06-25"]],
    "janelas_aumento": [["BR", "10", "8", "40", "35", "12", ""]],
    "distribuicao": [["classificacao", "BR", "covid19", "40"], ["faixa_etaria", "SP", "60+", "2"],
                     ["faixa_etaria", "BR", "ignorado", "1"]],
    "dicionario_trechos": [["1", "UTI", "7", "Campo UTI: internado em UTI?\nsim ou não", VETOR],
                           ["2", "", "8", "Texto sem campo", VETOR]],
}
# fmt: on


def escrever_csv(caminho: Path, cabecalho: list[str], linhas: list[list[str]]) -> None:
    """Como o COPY ... CSV escreve: \\n no fim da linha, aspas só quando precisa."""
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo, lineterminator="\n")
        escritor.writerow(cabecalho)
        escritor.writerows(linhas)


def montar(pasta: Path, linhas: dict[str, list[list[str]]] | None = None,
           cabecalhos: dict[str, list[str]] | None = None) -> pacote.Manifesto:  # fmt: skip
    pasta.mkdir(parents=True, exist_ok=True)
    for tabela, (colunas, _) in TABELAS_DEMO.items():
        cabecalho = (cabecalhos or {}).get(tabela, list(colunas))
        escrever_csv(pasta / f"{tabela}.csv", cabecalho, (linhas or LINHAS_VALIDAS)[tabela])
    return pacote.montar_manifesto(pasta, gerado_em=GERADO_EM, versao_codigo="abc1234")


@pytest.fixture
def pasta(tmp_path) -> Path:
    destino = tmp_path / "pacote"
    montar(destino)
    return destino


def _trocar(linhas: dict, tabela: str, linha: int, coluna: str, valor: str) -> dict:
    copia = {t: [list(r) for r in rs] for t, rs in linhas.items()}
    copia[tabela][linha][TABELAS_DEMO[tabela][0].index(coluna)] = valor
    return copia


def _manifesto_cru(pasta: Path) -> dict:
    return json.loads((pasta / pacote.ARQUIVO_MANIFESTO).read_text(encoding="utf-8"))


def _gravar_manifesto(pasta: Path, dados: dict) -> None:
    (pasta / pacote.ARQUIVO_MANIFESTO).write_text(json.dumps(dados), encoding="utf-8")


# --- pacote válido ---------------------------------------------------------------------------
def test_pacote_valido_passa_em_tudo(pasta):
    manifesto = pacote.conferir_pacote(pasta)
    assert pacote.conferir_sem_pii(pasta) == []
    assert set(manifesto.tabelas) == set(TABELAS_DEMO)
    assert manifesto.formato == 1 and manifesto.dimensao == 384
    assert manifesto.tabelas["casos_diarios"].linhas == 3
    assert manifesto.tabelas["casos_diarios"].colunas == TABELAS_DEMO["casos_diarios"][0]


def test_texto_com_quebra_de_linha_conta_uma_linha(pasta):
    # o COPY põe entre aspas o texto com \n: contar \n no arquivo daria 3, não 2
    assert pacote.ler_manifesto(pasta).tabelas["dicionario_trechos"].linhas == 2


def test_sha256_do_manifesto_e_o_do_arquivo_gravado(pasta):
    manifesto = pacote.ler_manifesto(pasta)
    assert manifesto.sha256 == pacote.sha256_arquivo(pasta / pacote.ARQUIVO_MANIFESTO)
    assert re.fullmatch(r"[0-9a-f]{64}", manifesto.sha256)


# --- sha256 determinístico -------------------------------------------------------------------
def test_mesmo_conteudo_mesmo_sha256(tmp_path):
    um = montar(tmp_path / "um")
    dois = montar(tmp_path / "dois")
    assert {t: m.sha256 for t, m in um.tabelas.items()} == {
        t: m.sha256 for t, m in dois.tabelas.items()
    }
    assert um.sha256 == dois.sha256  # mesmo gerado_em e versão: manifesto idêntico também


def test_conteudo_diferente_muda_o_sha256_da_tabela(tmp_path):
    um = montar(tmp_path / "um")
    dois = montar(tmp_path / "dois", _trocar(LINHAS_VALIDAS, "casos_diarios", 0, "casos", "8"))
    assert um.tabelas["casos_diarios"].sha256 != dois.tabelas["casos_diarios"].sha256
    assert um.tabelas["indicadores"].sha256 == dois.tabelas["indicadores"].sha256


# --- manifesto adulterado --------------------------------------------------------------------
def test_csv_alterado_depois_do_manifesto_reprova(pasta):
    caminho = pasta / "casos_diarios.csv"
    caminho.write_text(caminho.read_text(encoding="utf-8").replace(",7", ",70"), encoding="utf-8")
    with pytest.raises(PacoteInvalido, match="sha256"):
        pacote.conferir_pacote(pasta)


@pytest.mark.parametrize(
    "mexer",
    [
        lambda m: m.update(formato=2),
        lambda m: m["tabelas"]["casos_diarios"].update(linhas=4),
        lambda m: m["tabelas"]["casos_diarios"].update(colunas=["uf", "dia"]),
        lambda m: m["tabelas"]["casos_diarios"].update(arquivo="../casos_diarios.csv"),
        lambda m: m["tabelas"]["casos_diarios"].update(arquivo="/etc/passwd"),
        lambda m: m["tabelas"]["casos_diarios"].update(sha256="0" * 64),
        lambda m: m["tabelas"].pop("referencia"),
        lambda m: m["tabelas"].update(casos_brutos=m["tabelas"]["casos_diarios"]),
        lambda m: m.update(dimensao=768),
        lambda m: m.update(versao_codigo="abc; DROP TABLE x"),
        lambda m: m.update(gerado_em="ontem"),
        lambda m: m.update(gerado_em="2026-10-18T12:00:00"),  # sem fuso
    ],
    ids=["formato", "linhas", "colunas", "caminho_relativo", "caminho_absoluto", "sha256",
         "tabela_faltando", "tabela_a_mais", "dimensao", "versao", "data", "data_sem_fuso"],
)  # fmt: skip
def test_manifesto_adulterado_reprova(pasta, mexer):
    dados = _manifesto_cru(pasta)
    mexer(dados)
    _gravar_manifesto(pasta, dados)
    with pytest.raises(PacoteInvalido):
        pacote.conferir_pacote(pasta)


def test_manifesto_ilegivel_reprova(pasta):
    (pasta / pacote.ARQUIVO_MANIFESTO).write_text("{nao é json", encoding="utf-8")
    with pytest.raises(PacoteInvalido, match="manifesto"):
        pacote.ler_manifesto(pasta)


def test_manifesto_ausente_reprova(pasta):
    (pasta / pacote.ARQUIVO_MANIFESTO).unlink()
    with pytest.raises(PacoteInvalido, match="manifesto"):
        pacote.ler_manifesto(pasta)


def test_arquivo_estranho_na_pasta_reprova(pasta):
    escrever_csv(pasta / "casos_brutos.csv", ["nu_notific"], [["1"]])
    with pytest.raises(PacoteInvalido, match=r"casos_brutos\.csv"):
        pacote.conferir_pacote(pasta)
    assert any("casos_brutos.csv" in p for p in pacote.conferir_sem_pii(pasta))


def test_csv_que_e_link_simbolico_reprova(pasta, tmp_path):
    fora = tmp_path / "fora.csv"
    fora.write_bytes((pasta / "casos_diarios.csv").read_bytes())
    (pasta / "casos_diarios.csv").unlink()
    (pasta / "casos_diarios.csv").symlink_to(fora)
    with pytest.raises(PacoteInvalido, match="link"):
        pacote.conferir_pacote(pasta)


# --- colunas: lista fechada e nomes proibidos -------------------------------------------------
@pytest.mark.parametrize(
    "nome",
    ["nu_notific", "NU_NOTIFIC", "dt_nasc", "id_municip", "co_mun_res", "id_mn_resi", "cs_raca",
     "cs_sexo", "nm_pacient", "nm_mae_pac", "obs_evol", "obs", "sor_out", "id_caso", "cs_etinia",
     "cs_escol_n", "pac_cocbo", "pac_dscbo", "tem_cpf", "pov_ct", "estrang", "lote_1_cov",
     "id_unidade", "co_uni_not"],
)  # fmt: skip
def test_nome_proibido_e_reconhecido(nome):
    assert pacote.coluna_proibida(nome)


@pytest.mark.parametrize("nome", ["uf", "dia", "casos", "categoria", "texto", "campo", "id"])
def test_nome_comum_nao_e_proibido(nome):
    assert not pacote.coluna_proibida(nome)


def test_lista_permitida_nao_tem_nome_proibido():
    for colunas, _ in TABELAS_DEMO.values():
        assert not [c for c in colunas if pacote.coluna_proibida(c)]


def test_lista_proibida_cobre_o_adr_0004():
    # As colunas que o ADR 0004 tira da silver, citadas por nome, são proibidas no pacote.
    adr = (RAIZ_PROJETO / "docs/adr/0004-silver-sem-colunas-sensiveis.md").read_text("utf-8")
    trecho = adr.split("**não leva**", 1)[1].split("- Fica:", 1)[0]  # só a lista do que sai
    citadas = set(re.findall(r"`([A-Z][A-Z0-9_]{2,})`", trecho))
    assert {"NU_NOTIFIC", "DT_NASC", "CS_RACA", "CS_SEXO"} <= citadas  # a leitura achou a lista
    assert [c for c in citadas if not pacote.coluna_proibida(c)] == []
    assert pacote.coluna_proibida("id_caso")  # "NU_NOTIFIC vira id_caso": a chave também fica


def test_padroes_proibidos_sao_minusculos():
    assert all(p == p.lower() for p in COLUNAS_PROIBIDAS)


def test_coluna_fora_da_lista_reprova(tmp_path):
    destino = tmp_path / "p"
    linhas = {t: [list(r) for r in rs] for t, rs in LINHAS_VALIDAS.items()}
    linhas["casos_diarios"] = [[*r, "F"] for r in linhas["casos_diarios"]]
    montar(destino, linhas, {"casos_diarios": ["uf", "dia", "casos", "cs_sexo"]})
    problemas = pacote.conferir_sem_pii(destino)
    assert any("cs_sexo" in p and "proibida" in p for p in problemas)
    assert any("cs_sexo" in p and "fora da lista" in p for p in problemas)
    with pytest.raises(PacoteInvalido):
        pacote.conferir_pacote(destino)


def test_coluna_permitida_em_outra_tabela_reprova(tmp_path):
    # "texto" é permitida no dicionário, não nos casos diários
    destino = tmp_path / "p"
    linhas = {t: [list(r) for r in rs] for t, rs in LINHAS_VALIDAS.items()}
    linhas["casos_diarios"] = [[*r, "x"] for r in linhas["casos_diarios"]]
    montar(destino, linhas, {"casos_diarios": ["uf", "dia", "casos", "texto"]})
    assert any("texto" in p for p in pacote.conferir_sem_pii(destino))


def test_colunas_fora_de_ordem_reprovam_no_pacote(tmp_path):
    destino = tmp_path / "p"
    linhas = {t: [list(r) for r in rs] for t, rs in LINHAS_VALIDAS.items()}
    linhas["casos_diarios"] = [[r[1], r[0], r[2]] for r in linhas["casos_diarios"]]
    montar(destino, linhas, {"casos_diarios": ["dia", "uf", "casos"]})
    with pytest.raises(PacoteInvalido, match="colunas"):
        pacote.conferir_pacote(destino)


# --- colunas da gold antes de exportar --------------------------------------------------------
def test_gold_igual_a_lista_nao_tem_problema():
    colunas = {t: list(c) for t, (c, _) in TABELAS_DEMO.items()}
    assert pacote.conferir_colunas_gold(colunas) == []


def test_coluna_nova_na_gold_faz_a_exportacao_falhar():
    colunas = {t: list(c) for t, (c, _) in TABELAS_DEMO.items()}
    colunas["indicadores"].append("cs_sexo_masc")
    problemas = pacote.conferir_colunas_gold(colunas)
    assert len(problemas) == 1
    assert "cs_sexo_masc" in problemas[0] and "não está na lista" in problemas[0]


def test_tabela_ausente_ou_coluna_sumida_na_gold_falha():
    colunas = {t: list(c) for t, (c, _) in TABELAS_DEMO.items()}
    colunas.pop("distribuicao")
    colunas["referencia"].remove("total_casos")
    problemas = " ".join(pacote.conferir_colunas_gold(colunas))
    assert "distribuicao" in problemas and "total_casos" in problemas


# --- domínios dos valores ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "tabela,linha,coluna,valor",
    [
        ("casos_diarios", 0, "uf", "XX"),
        ("casos_diarios", 0, "uf", "Maria da Silva"),
        ("casos_diarios", 0, "uf", ""),
        ("casos_diarios", 0, "casos", "-1"),
        ("casos_diarios", 0, "casos", "1.5"),
        ("casos_diarios", 0, "casos", ""),
        ("casos_diarios", 0, "casos", "99999999999"),
        ("casos_diarios", 0, "dia", "24/06/2025"),
        ("casos_diarios", 0, "dia", "2025-02-30"),
        ("distribuicao", 0, "dimensao", "sexo"),
        ("distribuicao", 0, "categoria", "60+"),  # faixa etária na dimensão classificação
        ("distribuicao", 1, "categoria", "Fulano, 3 anos"),
        ("indicadores", 0, "obitos", "1e3"),
        ("referencia", 0, "total_casos", ""),
        ("dicionario_trechos", 0, "campo", "UTI; DROP"),
        ("dicionario_trechos", 0, "campo", "uti"),
        ("dicionario_trechos", 0, "pagina", "-2"),
        ("dicionario_trechos", 0, "embedding", "[" + ",".join(["0.1"] * 383) + "]"),
        ("dicionario_trechos", 0, "embedding", "[" + ",".join(["nan"] * 384) + "]"),
        ("dicionario_trechos", 0, "embedding", "texto"),
        ("dicionario_trechos", 0, "texto", ""),
    ],
)
def test_valor_fora_do_dominio_reprova(tmp_path, tabela, linha, coluna, valor):
    destino = tmp_path / "p"
    montar(destino, _trocar(LINHAS_VALIDAS, tabela, linha, coluna, valor))
    problemas = pacote.conferir_sem_pii(destino)
    assert problemas, f"{tabela}.{coluna}={valor!r} deveria reprovar"
    assert all(f"{tabela}.csv" in p for p in problemas)
    with pytest.raises(PacoteInvalido):
        pacote.conferir_pacote(destino)


def test_toda_coluna_permitida_tem_tipo():
    for colunas, _ in TABELAS_DEMO.values():
        assert [c for c in colunas if c not in pacote.TIPOS] == []


def test_gold_vazia_nao_vira_pacote(tmp_path):
    destino = tmp_path / "p"
    montar(destino, _trocar(LINHAS_VALIDAS, "referencia", 0, "total_casos", "0"))
    with pytest.raises(PacoteInvalido, match="gold vazia"):
        pacote.conferir_pacote(destino)


def test_referencia_com_duas_linhas_reprova(tmp_path):
    destino = tmp_path / "p"
    linhas = {t: [list(r) for r in rs] for t, rs in LINHAS_VALIDAS.items()}
    linhas["referencia"] = linhas["referencia"] * 2
    montar(destino, linhas)
    with pytest.raises(PacoteInvalido, match="exatamente uma linha"):
        pacote.conferir_pacote(destino)


def test_mensagem_nao_repete_o_valor_recusado(tmp_path):
    destino = tmp_path / "p"
    montar(destino, _trocar(LINHAS_VALIDAS, "casos_diarios", 1, "uf", "Maria da Silva"))
    problemas = pacote.conferir_sem_pii(destino)
    assert problemas and "Maria" not in " ".join(problemas)
    assert "linha 3" in problemas[0] and "uf" in problemas[0]  # linha do arquivo (com cabeçalho)


def test_muitos_problemas_na_mesma_coluna_sao_resumidos(tmp_path):
    destino = tmp_path / "p"
    linhas = {t: [list(r) for r in rs] for t, rs in LINHAS_VALIDAS.items()}
    linhas["casos_diarios"] = [["XX", "2025-06-24", "1"]] * 50
    montar(destino, linhas)
    problemas = pacote.conferir_sem_pii(destino)
    assert len(problemas) <= pacote.PROBLEMAS_POR_COLUNA + 1
    assert any("mais" in p for p in problemas)


def test_texto_publico_com_telefone_vira_aviso_e_nao_reprova(tmp_path):
    destino = tmp_path / "p"
    texto = "Dúvidas: ligue (61) 3315-2425 ou escreva para notifica@saude.gov.br"
    montar(destino, _trocar(LINHAS_VALIDAS, "dicionario_trechos", 1, "texto", texto))
    assert pacote.conferir_sem_pii(destino) == []
    avisos = pacote.avisos_pii(destino)
    assert avisos and all("dicionario_trechos.csv" in a for a in avisos)
    assert "3315" not in " ".join(avisos)
    pacote.conferir_pacote(destino)  # não levanta


# --- gravação atômica: nunca apaga o que não é pacote -----------------------------------------
def test_publicar_substitui_pacote_antigo(tmp_path):
    antigo = tmp_path / "pacote"
    montar(antigo)
    novo = tmp_path / ".novo"
    montar(novo, _trocar(LINHAS_VALIDAS, "casos_diarios", 0, "casos", "8"))
    esperado = pacote.ler_manifesto(novo).tabelas["casos_diarios"].sha256
    pacote.publicar_pasta(novo, antigo)
    assert not novo.exists()
    assert pacote.ler_manifesto(antigo).tabelas["casos_diarios"].sha256 == esperado
    assert [p.name for p in tmp_path.iterdir()] == ["pacote"]  # nada de pasta temporária sobrando


def test_publicar_recusa_pasta_que_nao_e_pacote(tmp_path):
    destino = tmp_path / "documentos"
    destino.mkdir()
    (destino / "importante.txt").write_text("não apagar", encoding="utf-8")
    novo = tmp_path / ".novo"
    montar(novo)
    with pytest.raises(PacoteInvalido, match="não é um pacote"):
        pacote.publicar_pasta(novo, destino)
    assert (destino / "importante.txt").read_text(encoding="utf-8") == "não apagar"


def test_publicar_recusa_pacote_com_arquivo_a_mais(tmp_path):
    destino = tmp_path / "pacote"
    montar(destino)
    (destino / "notas.txt").write_text("minhas notas", encoding="utf-8")
    novo = tmp_path / ".novo"
    montar(novo)
    with pytest.raises(PacoteInvalido, match="não é um pacote"):
        pacote.publicar_pasta(novo, destino)
    assert (destino / "notas.txt").exists()


def test_publicar_em_pasta_nova(tmp_path):
    novo = tmp_path / ".novo"
    montar(novo)
    destino = tmp_path / "sub" / "pacote"
    destino.parent.mkdir()
    pacote.publicar_pasta(novo, destino)
    pacote.conferir_pacote(destino)
