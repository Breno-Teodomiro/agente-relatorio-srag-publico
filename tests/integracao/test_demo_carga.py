"""Carga do pacote no banco da demo (simulado no Compose): efeito no banco, não status."""

import csv
import shutil
import threading
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import psycopg
import pytest

from srag_agente.banco import conectar
from srag_agente.demo import TABELAS_DEMO, carregar, pacote
from srag_agente.demo.carregar import COLUNAS_ESPERADAS, RecusaCarga
from srag_agente.demo.pacote import PacoteInvalido
from srag_agente.ferramentas.dicionario import BuscaDicionario
from srag_agente.metricas.repositorio import RepositorioGold
from tests.fakes import embedder_falso

pytestmark = pytest.mark.integracao


def _carregar(cfg, pasta, **opcoes):
    return carregar.carregar(cfg, pasta, alvo=cfg.pg_host, permitir_local=True, **opcoes)


def _contagens(cfg) -> dict[str, int]:
    with conectar(cfg, "admin", autocommit=True) as conn:
        return {t: conn.execute(f"SELECT count(*) FROM gold.{t}").fetchone()[0]
                for t in TABELAS_DEMO}  # fmt: skip


def _esquemas(cfg) -> set[str]:
    with conectar(cfg, "admin", autocommit=True) as conn:
        return {r[0] for r in conn.execute("SELECT nspname FROM pg_namespace")}


def _copiar_pacote(origem: Path, destino: Path) -> Path:
    shutil.copytree(origem, destino)
    return destino


def _trocar_dicionario(pasta: Path, linhas: list[list[str]]) -> None:
    colunas = TABELAS_DEMO["dicionario_trechos"][0]
    with (pasta / "dicionario_trechos.csv").open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo, lineterminator="\n")
        escritor.writerow(colunas)
        escritor.writerows(linhas)
    manifesto = pacote.ler_manifesto(pasta)
    pacote.montar_manifesto(pasta, gerado_em=manifesto.gerado_em, versao_codigo="teste")


def _vetor_texto(vetor: list[float]) -> str:
    return "[" + ",".join(repr(float(np.float32(v))) for v in vetor) + "]"


# --- carga completa ----------------------------------------------------------------------------
def test_carga_completa_confere_tudo(carga_demo):
    resumo = carga_demo.resumo
    falhas = [c for c in resumo.conferencias if not c.ok]
    assert falhas == [] and resumo.ok
    assert resumo.sha256 == carga_demo.manifesto.sha256
    esperado = {t: m.linhas for t, m in carga_demo.manifesto.tabelas.items()}
    assert resumo.linhas == esperado
    assert _contagens(carga_demo.cfg) == esperado


def test_gold_da_demo_e_a_gold_local(carga_demo, cfg_teste, tmp_path):
    # Volta completa: exportar do banco da demo dá os mesmos bytes do pacote que entrou (os
    # vetores inclusive, pela representação de texto do pgvector).
    de_volta = pacote.exportar(carga_demo.cfg, tmp_path / "volta")
    assert {t: m.sha256 for t, m in de_volta.tabelas.items()} == {
        t: m.sha256 for t, m in carga_demo.manifesto.tabelas.items()
    }
    with conectar(carga_demo.cfg, "agente", autocommit=True) as conn:
        ultimo = conn.execute(
            "SELECT sha256, versao_codigo FROM gold.pacote_demo ORDER BY carregado_em DESC LIMIT 1"
        ).fetchone()
    assert ultimo[0] == carga_demo.manifesto.sha256


def test_tabelas_nascem_com_os_tipos_esperados(carga_demo):
    with conectar(carga_demo.cfg, "admin") as conn:
        for tabela, esperadas in COLUNAS_ESPERADAS.items():
            assert carregar._colunas_atuais(conn, tabela) == list(esperadas), tabela


def test_codigo_da_aplicacao_le_as_tabelas_da_demo(carga_demo):
    # RepositorioGold e as séries rodam sem mudar uma linha de SQL contra as tabelas da demo.
    with (carga_demo.pacote / "referencia.csv").open(encoding="utf-8", newline="") as arquivo:
        esperado = next(csv.DictReader(arquivo))
    with conectar(carga_demo.cfg, "agente", autocommit=True) as conn:
        repositorio = RepositorioGold(conn)
        referencia = repositorio.referencia()
        assert str(referencia.data_referencia) == esperado["data_referencia"]
        assert referencia.total_casos == int(esperado["total_casos"]) > 0
        assert repositorio.indicadores("BR")["total_casos"] == referencia.total_casos
        assert len(repositorio.serie_diaria("BR")) == 30
        assert repositorio.distribuicao("faixa_etaria", "BR")


# --- idempotência e trilha --------------------------------------------------------------------
def test_carregar_duas_vezes_preserva_a_trilha(banco_descartavel, carga_demo):
    cfg = banco_descartavel
    _carregar(cfg, carga_demo.pacote)
    id_execucao = uuid4()
    with conectar(cfg, "auditor", autocommit=True) as conn:
        conn.execute(
            "INSERT INTO auditoria.execucoes (id_execucao, provedor, modelo, parametros, "
            "versao_codigo, hash_prompts) VALUES (%s, 'p', 'm', '{}', 'v', 'h')",
            (id_execucao,),
        )
    segundo = _carregar(cfg, carga_demo.pacote)
    assert segundo.ok and segundo.papeis_criados == () and segundo.senhas_enviadas == ()
    assert _contagens(cfg) == {t: m.linhas for t, m in carga_demo.manifesto.tabelas.items()}
    with conectar(cfg, "admin", autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM auditoria.execucoes WHERE id_execucao = %s",
                            (id_execucao,)).fetchone()[0] == 1  # fmt: skip
        assert conn.execute("SELECT count(*) FROM gold.pacote_demo").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM auditoria.limites_demo").fetchone()[0] == 1


def test_duas_cargas_ao_mesmo_tempo_fazem_fila(banco_descartavel, carga_demo):
    cfg = banco_descartavel
    resultados, erros = [], []

    def carga():
        try:
            resultados.append(_carregar(cfg, carga_demo.pacote))
        except Exception as erro:  # registra para o assert, não esconde
            erros.append(erro)

    linhas = [threading.Thread(target=carga) for _ in range(2)]
    for linha in linhas:
        linha.start()
    for linha in linhas:
        linha.join(timeout=120)
    assert erros == [] and len(resultados) == 2
    assert _contagens(cfg) == {t: m.linhas for t, m in carga_demo.manifesto.tabelas.items()}


# --- vetores e busca ----------------------------------------------------------------------------
def test_vetores_identicos_depois_da_volta(banco_descartavel, carga_demo, tmp_path):
    textos = ["Campo UTI: internado em UTI?", "Campo VACINA_COV: vacina contra covid",
              "Trecho sem campo, com vírgula, aspas \" e\nquebra de linha"]  # fmt: skip
    vetores = embedder_falso(textos)
    vetores[2][0], vetores[2][1] = 1e-07, -0.333333343  # notação científica e negativo
    linhas = [
        [str(i), campo, "3", texto, _vetor_texto(vetor)]
        for i, (campo, texto, vetor) in enumerate(
            zip(["UTI", "VACINA_COV", ""], textos, vetores, strict=True), 1
        )
    ]
    pasta = _copiar_pacote(carga_demo.pacote, tmp_path / "sintetico")
    _trocar_dicionario(pasta, linhas)
    _carregar(banco_descartavel, pasta)

    with conectar(banco_descartavel, "admin", autocommit=True) as conn:
        no_banco = conn.execute("SELECT embedding::text FROM gold.dicionario_trechos ORDER BY id")
        lidos = [np.array(v[0][1:-1].split(","), dtype=np.float32) for v in no_banco]
    assert all(np.array_equal(a, np.array(b, dtype=np.float32)) for a, b in
               zip(lidos, vetores, strict=True))  # fmt: skip

    volta = pacote.exportar(banco_descartavel, tmp_path / "volta")
    _carregar(banco_descartavel, tmp_path / "volta")
    de_novo = pacote.exportar(banco_descartavel, tmp_path / "de_novo")
    assert de_novo.tabelas["dicionario_trechos"].sha256 == volta.tabelas[
        "dicionario_trechos"].sha256  # fmt: skip

    with conectar(banco_descartavel, "agente", autocommit=True) as conn:
        trechos = BuscaDicionario(conn, embedder_falso, k=2).buscar("o que é VACINA_COV?")
    assert trechos and trechos[0].campo == "VACINA_COV"


def test_texto_com_sql_e_so_dado(banco_descartavel, carga_demo, tmp_path):
    malicioso = "x'); DROP TABLE gold.referencia; --"
    pasta = _copiar_pacote(carga_demo.pacote, tmp_path / "p")
    _trocar_dicionario(pasta, [["1", "", "1", malicioso, _vetor_texto([0.5] * 384)]])
    _carregar(banco_descartavel, pasta)
    with conectar(banco_descartavel, "admin", autocommit=True) as conn:
        assert conn.execute("SELECT texto FROM gold.dicionario_trechos").fetchone()[0] == malicioso
        assert conn.execute("SELECT count(*) FROM gold.referencia").fetchone()[0] == 1


# --- recusas: nada muda ---------------------------------------------------------------------------
def test_esquema_divergente_recusa_sem_drop(banco_descartavel, carga_demo):
    cfg = banco_descartavel
    with conectar(cfg, "admin", autocommit=True) as conn:
        conn.execute("CREATE SCHEMA gold")
        conn.execute("CREATE TABLE gold.casos_diarios (uf text, dia date, casos int, extra text)")
        conn.execute("INSERT INTO gold.casos_diarios VALUES ('SP', '2025-01-01', 1, 'meu')")
    with pytest.raises(RecusaCarga, match="divergente"):
        _carregar(cfg, carga_demo.pacote)
    with conectar(cfg, "admin", autocommit=True) as conn:
        assert conn.execute("SELECT extra FROM gold.casos_diarios").fetchall() == [("meu",)]
        objetos = conn.execute(
            "SELECT relname FROM pg_class c JOIN pg_namespace n ON "
            "n.oid = c.relnamespace WHERE nspname = 'gold' AND relkind = 'r'"
        )
        assert {r[0] for r in objetos} == {"casos_diarios"}
    assert "auditoria" not in _esquemas(cfg)


def test_view_no_lugar_da_tabela_e_divergente(banco_descartavel, carga_demo):
    with conectar(banco_descartavel, "admin", autocommit=True) as conn:
        conn.execute("CREATE SCHEMA gold")
        conn.execute("CREATE VIEW gold.referencia AS SELECT current_date AS data_referencia, "
                     "current_date AS data_consolidada, current_date AS inicio_base, "
                     "1 AS total_casos")  # fmt: skip
    with pytest.raises(RecusaCarga, match="divergente"):
        _carregar(banco_descartavel, carga_demo.pacote)


@pytest.fixture
def papeis_intocados(monkeypatch):
    """Espião: a recusa tem de vir ANTES de qualquer escrita (papel, senha, SQL da demo)."""
    chamadas = []
    monkeypatch.setattr(carregar, "_preparar_papeis", lambda *a, **k: chamadas.append(1))
    yield chamadas
    assert chamadas == [], "a carga chegou aos papéis antes de recusar o banco"


@pytest.mark.parametrize("esquema", ["raw", "silver"])
def test_banco_com_raw_e_recusado(banco_descartavel, carga_demo, papeis_intocados, esquema):
    with conectar(banco_descartavel, "admin", autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {esquema}")
    with pytest.raises(RecusaCarga, match="parece o banco local"):
        _carregar(banco_descartavel, carga_demo.pacote)
    assert "gold" not in _esquemas(banco_descartavel)


def test_banco_local_de_verdade_e_recusado(cfg_teste, banco_carregado, carga_demo,
                                           papeis_intocados):  # fmt: skip
    # o srag_teste tem raw e silver: mesmo com permitir_local, a carga não escreve nele
    with pytest.raises(RecusaCarga, match="parece o banco local"):
        carregar.carregar(cfg_teste, carga_demo.pacote, alvo=cfg_teste.pg_host,
                          permitir_local=True)  # fmt: skip


def test_pacote_adulterado_nao_abre_conexao(banco_descartavel, carga_demo, tmp_path):
    pasta = _copiar_pacote(carga_demo.pacote, tmp_path / "p")
    with (pasta / "casos_diarios.csv").open("a", encoding="utf-8") as arquivo:
        arquivo.write("SP,2020-01-01,1\n")
    with pytest.raises(PacoteInvalido, match="sha256"):
        _carregar(banco_descartavel, pasta)
    assert "gold" not in _esquemas(banco_descartavel)


def test_falha_na_conferencia_desfaz_a_carga_inteira(banco_descartavel, carga_demo, monkeypatch):
    monkeypatch.setattr(carregar, "_conferir_dentro", lambda conn, lido: ["simulada"])
    with pytest.raises(RecusaCarga, match="simulada"):
        _carregar(banco_descartavel, carga_demo.pacote)
    assert {"gold", "auditoria"}.isdisjoint(_esquemas(banco_descartavel))


def test_gold_com_tabela_a_mais_e_recusada(banco_descartavel, carga_demo):
    # o GRANT SELECT ON ALL TABLES daria a tabela estranha ao agente: a carga não confirma
    with conectar(banco_descartavel, "admin", autocommit=True) as conn:
        conn.execute("CREATE SCHEMA gold")
        conn.execute("CREATE TABLE gold.casos_brutos (nu_notific text)")
    with pytest.raises(RecusaCarga, match="casos_brutos"):
        _carregar(banco_descartavel, carga_demo.pacote)
    with conectar(banco_descartavel, "admin", autocommit=True) as conn:
        assert not conn.execute(
            "SELECT has_table_privilege('srag_agente', 'gold.casos_brutos', 'SELECT')"
        ).fetchone()[0]


def test_objeto_fora_da_demo_e_recusado_com_nome_e_tipo(banco_descartavel, carga_demo):
    with conectar(banco_descartavel, "admin", autocommit=True) as conn:
        conn.execute("CREATE TABLE public.sobra (x int)")
        conn.execute("CREATE VIEW public.espia AS SELECT 1 AS x")
    with pytest.raises(RecusaCarga, match="fora de gold/auditoria") as recusa:
        _carregar(banco_descartavel, carga_demo.pacote)
    assert "public.sobra (tabela)" in str(recusa.value)
    assert "public.espia (view)" in str(recusa.value)


def test_objeto_de_extensao_do_provedor_nao_impede_a_carga(banco_descartavel, carga_demo,
                                                             cfg_teste):  # fmt: skip
    # O provedor pode instalar extensão própria com view em public: os objetos dela não contam
    # como "fora da demo" e a carga confirma; mas a extensão fora da lista reprova a conferência
    # (o dono decide), listada só pelo nome.
    with conectar(cfg_teste, "admin", banco=banco_descartavel.pg_banco, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_buffercache")
    resumo = _carregar(banco_descartavel, carga_demo.pacote)
    falhas = {c.tentativa: c.obtido for c in resumo.conferencias if not c.ok}
    assert list(falhas) == ["extensões fora de plpgsql/vector"]
    assert falhas["extensões fora de plpgsql/vector"] == "pg_buffercache"
    assert any("pg_buffercache" in aviso for aviso in resumo.avisos)
    assert _contagens(banco_descartavel)["referencia"] == 1  # a carga foi confirmada


def test_leitor_continua_lendo_durante_a_carga(banco_descartavel, carga_demo):
    # TRUNCATE pediria ACCESS EXCLUSIVE: esperaria o leitor de transação aberta e enfileiraria
    # os novos leitores atrás dele, que cairiam no statement_timeout (5 s) do agente no meio de
    # um relatório. A troca por DELETE não bloqueia leitura: todos veem o retrato antigo.
    cfg = banco_descartavel
    _carregar(cfg, carga_demo.pacote)
    resultado: dict[str, object] = {}

    def carga():
        try:
            _carregar(cfg, carga_demo.pacote)
            resultado["carga"] = "ok"
        except Exception as erro:  # registra para o assert
            resultado["carga"] = type(erro).__name__

    with conectar(cfg, "agente") as leitor:  # transação aberta, como um relatório em andamento
        antes = leitor.execute("SELECT total_casos FROM gold.referencia").fetchone()[0]
        linha = threading.Thread(target=carga)
        linha.start()
        time.sleep(2)
        with conectar(cfg, "agente", autocommit=True) as novo:
            assert novo.execute("SELECT total_casos FROM gold.referencia").fetchone()[0] == antes
        assert leitor.execute("SELECT count(*) FROM gold.casos_diarios").fetchone()[0] > 0
        leitor.commit()
        linha.join(timeout=120)
    assert resultado["carga"] == "ok"


# --- senha em texto (provedor que recusa hash), feita pelo dono sem superusuário ----------------
def test_dono_cria_papel_com_senha_em_texto(banco_descartavel, cfg_teste):
    from srag_agente.dados.bootstrap import _comando_papel

    usuario, senha = f"srag_teste_papel_{uuid4().hex[:8]}", uuid4().hex
    with conectar(banco_descartavel, "admin", autocommit=True) as conn:
        conn.execute(_comando_papel(conn, usuario, senha, existe=False, em_hash=False))
    try:
        cfg = banco_descartavel
        with psycopg.connect(host=cfg.pg_host, port=cfg.pg_porta, dbname=cfg.pg_banco,
                             user=usuario, password=senha, connect_timeout=5) as conn:  # fmt: skip
            assert conn.execute("SELECT current_user").fetchone()[0] == usuario
    finally:
        with conectar(banco_descartavel, "admin", autocommit=True) as conn:
            conn.execute(f'DROP ROLE IF EXISTS "{usuario}"')


def test_trocar_senhas_em_texto_pelo_dono(banco_descartavel, carga_demo):
    # Reenvia a MESMA senha (a do .env local): o efeito é o ALTER ROLE pelo dono sem
    # superusuário, como no provedor, sem trocar a senha que as outras sessões usam.
    with pytest.raises(RecusaCarga, match="verify-full"):  # local sem TLS: recusa por padrão
        _carregar(banco_descartavel, carga_demo.pacote, senha_em_texto=True, trocar_senhas=True)
    resumo = _carregar(banco_descartavel, carga_demo.pacote, senha_em_texto=True,
                       trocar_senhas=True, aceitar_tls_sem_verificacao=True)  # fmt: skip
    assert resumo.senhas_enviadas == ("srag_agente", "srag_auditor")
    assert resumo.papeis_criados == ()
    assert resumo.ok  # a conferência entrou com as senhas: continuam valendo


def test_interrupcao_na_conferencia_depois_do_commit(banco_descartavel, carga_demo, monkeypatch):
    def interrompe(cfg):
        raise KeyboardInterrupt

    monkeypatch.setattr(carregar, "conferir_demo", interrompe)
    with pytest.raises(carregar.CargaConfirmada) as erro:
        _carregar(banco_descartavel, carga_demo.pacote)
    assert erro.value.interrompida and erro.value.sha256 == carga_demo.manifesto.sha256
    assert _contagens(banco_descartavel)["referencia"] == 1  # a gold já foi trocada
