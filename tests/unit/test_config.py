from pathlib import Path

import pytest
from pydantic import ValidationError

from srag_agente.agente.llm import ConfiguracaoInvalida
from srag_agente.config import RAIZ_PROJETO, Config, raiz_do_projeto
from srag_agente.demo import (
    COLUNAS_PROIBIDAS,
    FUSO_DEMO,
    HOSTS_LOCAIS,
    SQLSTATE_TETO,
    SSLMODES_SEGUROS,
    TABELAS_DEMO,
    TRAVA_TETO,
)

SENHAS = {
    "PG_SENHA_ADMIN": "adm 1",
    "PG_SENHA_ETL": "etl 1",
    "PG_SENHA_AGENTE": "agente 1",
    "PG_SENHA_AUDITOR": "auditor 1",
}


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    """Variável exportada no shell (PG_PORTA, LLM_PROVIDER…) não pode decidir o teste."""
    for campo in Config.model_fields:
        monkeypatch.delenv(campo.upper(), raising=False)


@pytest.fixture
def cfg(monkeypatch) -> Config:
    for chave, valor in SENHAS.items():
        monkeypatch.setenv(chave, valor)
    return Config(_env_file=None)


def test_dsn_usa_o_usuario_do_papel(cfg):
    dsn = cfg.dsn("agente")
    assert "user=srag_agente" in dsn
    assert "port=5435" in dsn
    assert "dbname=srag" in dsn
    assert "agente 1" in dsn


def test_dsn_aceita_outro_banco(cfg):
    assert "dbname=srag_teste" in cfg.dsn("etl", banco="srag_teste")


def test_admin_usa_postgres(cfg):
    assert "user=postgres" in cfg.dsn("admin")


def test_senha_nao_vaza_no_repr(cfg):
    assert "agente 1" not in repr(cfg)


def test_padroes_do_llm(cfg):
    assert cfg.llm_provider == "openrouter"
    assert cfg.llm_modelo_openrouter == "openai/gpt-4.1-mini"
    assert cfg.max_chamadas_ferramenta == 12


def test_variavel_definida_de_proposito_vale(monkeypatch):
    # a limpeza do autouse não desliga o ambiente: só tira o que veio do shell
    monkeypatch.setenv("PG_PORTA", "9999")
    for chave, valor in SENHAS.items():
        monkeypatch.setenv(chave, valor)
    assert Config(_env_file=None).pg_porta == 9999


# --- .env e caminhos não dependem da pasta de onde se chama a CLI -----------------------------
def test_env_file_e_o_da_raiz_do_projeto():
    assert (RAIZ_PROJETO / "pyproject.toml").is_file()
    assert Path(Config.model_config["env_file"]) == RAIZ_PROJETO / ".env"


def test_raiz_e_a_primeira_pasta_acima_com_pyproject(tmp_path):
    (tmp_path / "proj" / "src" / "pacote").mkdir(parents=True)
    (tmp_path / "proj" / "pyproject.toml").write_text("", encoding="utf-8")
    assert raiz_do_projeto(tmp_path / "proj" / "src" / "pacote") == tmp_path / "proj"


def test_sem_pyproject_acima_a_raiz_e_a_pasta_atual(tmp_path, monkeypatch):
    # pacote instalado fora do repositório (site-packages): cai na pasta de onde se chamou
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pacote").mkdir()
    assert raiz_do_projeto(tmp_path / "pacote", limite=tmp_path) == tmp_path


def test_caminho_relativo_e_resolvido_a_partir_da_raiz(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # chamar de outra pasta não muda para onde os caminhos apontam
    assert cfg.arquivo_dados == RAIZ_PROJETO / "dados/brutos/INFLUD25_DATASUS-Versao26-06-2025.zip"
    assert cfg.pasta_relatorios == RAIZ_PROJETO / "relatorios"
    absoluto = tmp_path / "outro.zip"
    assert cfg.pasta_cache_modelos.is_absolute()
    assert Config(_env_file=None, arquivo_dados=absoluto).arquivo_dados == absoluto


def test_erro_de_configuracao_nao_mostra_o_valor_recebido(monkeypatch):
    # sem isso o pydantic imprime input_value={'pg_senha_admin': 'segre...'} na tela/log
    monkeypatch.setenv("PG_SENHA_ADMIN", "segredo-que-nao-pode-vazar")
    with pytest.raises(ValidationError) as erro:
        Config(_env_file=None, openrouter_api_key="sk-chave-que-nao-pode-vazar")
    texto = str(erro.value) + repr(erro.value)
    assert "pg_senha_etl" in texto  # o campo que falta aparece
    assert "segre" not in texto and "sk-chave" not in texto


def test_limites_de_execucao_tem_os_padroes_do_spec(cfg):
    assert cfg.max_segundos_execucao == 300
    assert cfg.max_voltas_cobertura == 2
    assert cfg.max_tentativas_redacao == 3


# --- M2: tetos e preços com faixa e finitude --------------------------------------------------
@pytest.mark.parametrize(
    "variavel,valor",
    [
        ("MAX_SEGUNDOS_EXECUCAO", "nan"),
        ("MAX_SEGUNDOS_EXECUCAO", "inf"),
        ("MAX_SEGUNDOS_EXECUCAO", "0"),
        ("MAX_SEGUNDOS_EXECUCAO", "-5"),
        ("MAX_TOKENS_EXECUCAO", "0"),
        ("MAX_TOKENS_EXECUCAO", "-1"),
        ("MAX_TOKENS_EXECUCAO", "nan"),
        ("MAX_CHAMADAS_FERRAMENTA", "0"),
        ("MAX_TENTATIVAS_REDACAO", "0"),
        ("MAX_VOLTAS_COBERTURA", "-1"),
        ("PRECO_ENTRADA_USD_MTOK", "nan"),
        ("PRECO_ENTRADA_USD_MTOK", "-0.4"),
        ("PRECO_SAIDA_USD_MTOK", "inf"),
        ("PRECO_SAIDA_USD_MTOK", "-1"),
    ],
)
def test_teto_ou_preco_fora_da_faixa_e_erro_de_configuracao(cfg, monkeypatch, variavel, valor):
    monkeypatch.setenv(variavel, valor)
    with pytest.raises(ValidationError) as erro:
        Config(_env_file=None)
    assert variavel.lower() in str(erro.value)


@pytest.mark.parametrize(
    "variavel,valor",
    [("MAX_VOLTAS_COBERTURA", "0"), ("PRECO_ENTRADA_USD_MTOK", "0"), ("PRECO_SAIDA_USD_MTOK", "0")],
)
def test_zero_vale_onde_tem_sentido(cfg, monkeypatch, variavel, valor):
    # zero voltas = não insiste na cobertura (Limites aceita); preço zero = modelo gratuito
    monkeypatch.setenv(variavel, valor)
    assert getattr(Config(_env_file=None), variavel.lower()) == 0


# --- Demo online: conexão remota, senhas por modo e contrato ----------------------------------
def test_dsn_local_e_identico_ao_de_antes(cfg):
    # valores fixos do DSN que o código montava antes dos campos de TLS/timeout/admin
    esperado = {
        "admin": "host=localhost port=5435 dbname=srag user=postgres password='adm 1'",
        "etl": "host=localhost port=5435 dbname=srag user=srag_etl password='etl 1'",
        "agente": "host=localhost port=5435 dbname=srag user=srag_agente password='agente 1'",
        "auditor": "host=localhost port=5435 dbname=srag user=srag_auditor password='auditor 1'",
    }
    for papel, dsn in esperado.items():
        assert cfg.dsn(papel) == dsn
    assert cfg.dsn("etl", banco="srag_teste") == esperado["etl"].replace(
        "dbname=srag ", "dbname=srag_teste "
    )


def test_padroes_da_conexao_remota_sao_os_locais(cfg):
    assert cfg.pg_usuario_admin == "postgres"
    assert cfg.pg_sslmode is None and cfg.pg_sslrootcert is None
    assert cfg.pg_connect_timeout is None
    assert cfg.demo_online is False
    assert cfg.busca_dicionario == "vetor"


def test_tls_e_timeout_so_aparecem_no_dsn_quando_definidos(monkeypatch, cfg):
    monkeypatch.setenv("PG_SSLMODE", "verify-full")
    monkeypatch.setenv("PG_SSLROOTCERT", "system")
    monkeypatch.setenv("PG_CONNECT_TIMEOUT", "15")
    dsn = Config(_env_file=None).dsn("agente")
    assert "sslmode=verify-full" in dsn
    assert "sslrootcert=system" in dsn
    assert "connect_timeout=15" in dsn


def test_so_o_que_foi_definido_entra_no_dsn(monkeypatch, cfg):
    monkeypatch.setenv("PG_SSLMODE", "require")
    dsn = Config(_env_file=None).dsn("agente")
    assert "sslmode=require" in dsn
    assert "sslrootcert" not in dsn and "connect_timeout" not in dsn


@pytest.mark.parametrize(
    "variavel,valor",
    [("PG_SSLMODE", "qualquer"), ("PG_CONNECT_TIMEOUT", "0"), ("PG_CONNECT_TIMEOUT", "-3")],
)
def test_tls_ou_timeout_invalido_e_erro_de_configuracao(cfg, monkeypatch, variavel, valor):
    monkeypatch.setenv(variavel, valor)
    with pytest.raises(ValidationError) as erro:
        Config(_env_file=None)
    assert variavel.lower() in str(erro.value)


def test_admin_configuravel_so_afeta_o_papel_admin(monkeypatch, cfg):
    monkeypatch.setenv("PG_USUARIO_ADMIN", "neondb_owner")
    c = Config(_env_file=None)
    assert "user=neondb_owner" in c.dsn("admin")
    assert "user=srag_agente" in c.dsn("agente")
    assert "user=srag_etl" in c.dsn("etl")


@pytest.mark.parametrize(
    "faltando", ["PG_SENHA_ADMIN", "PG_SENHA_ETL", "PG_SENHA_AGENTE", "PG_SENHA_AUDITOR"]
)
def test_modo_local_exige_as_quatro_senhas(monkeypatch, faltando):
    for chave, valor in SENHAS.items():
        if chave != faltando:
            monkeypatch.setenv(chave, valor)
    with pytest.raises(ValidationError) as erro:
        Config(_env_file=None)
    assert faltando.lower() in str(erro.value)


def test_modo_demo_exige_so_agente_e_auditor(monkeypatch):
    monkeypatch.setenv("PG_SENHA_AGENTE", "agente 1")
    monkeypatch.setenv("PG_SENHA_AUDITOR", "auditor 1")
    c = Config(_env_file=None, demo_online=True)
    assert "user=srag_agente" in c.dsn("agente")
    assert "user=srag_auditor" in c.dsn("auditor")


@pytest.mark.parametrize("faltando", ["PG_SENHA_AGENTE", "PG_SENHA_AUDITOR"])
def test_modo_demo_sem_agente_ou_auditor_e_erro(monkeypatch, faltando):
    for chave in ("PG_SENHA_AGENTE", "PG_SENHA_AUDITOR"):
        if chave != faltando:
            monkeypatch.setenv(chave, "x")
    with pytest.raises(ValidationError) as erro:
        Config(_env_file=None, demo_online=True)
    assert faltando.lower() in str(erro.value)


def test_modo_demo_nao_entrega_senha_de_papel_que_nao_tem(monkeypatch):
    monkeypatch.setenv("PG_SENHA_AGENTE", "agente 1")
    monkeypatch.setenv("PG_SENHA_AUDITOR", "auditor 1")
    c = Config(_env_file=None, demo_online=True)
    for papel in ("admin", "etl"):
        with pytest.raises(ConfiguracaoInvalida) as erro:
            c.dsn(papel)
        assert f"PG_SENHA_{papel.upper()}" in str(erro.value)


def test_erro_do_validador_de_senhas_nao_mostra_valor(monkeypatch):
    monkeypatch.setenv("PG_SENHA_AGENTE", "segredo-que-nao-pode-vazar")
    with pytest.raises(ValidationError) as erro:
        Config(_env_file=None, demo_online=True)
    assert "segredo-que" not in str(erro.value) + repr(erro.value)


def test_busca_do_dicionario_so_aceita_vetor_ou_texto(cfg, monkeypatch):
    monkeypatch.setenv("BUSCA_DICIONARIO", "texto")
    assert Config(_env_file=None).busca_dicionario == "texto"
    monkeypatch.setenv("BUSCA_DICIONARIO", "outra")
    with pytest.raises(ValidationError):
        Config(_env_file=None)


def test_contrato_da_demo():
    assert SQLSTATE_TETO == "SR429" and len(SQLSTATE_TETO) == 5
    assert TRAVA_TETO == 7_350_302
    assert FUSO_DEMO == "America/Sao_Paulo"
    assert {"localhost", "127.0.0.1", "::1", "banco", "host.docker.internal"} == set(HOSTS_LOCAIS)
    assert set(SSLMODES_SEGUROS) == {"require", "verify-ca", "verify-full"}
    assert set(TABELAS_DEMO) == {
        "referencia",
        "casos_diarios",
        "indicadores",
        "janelas_aumento",
        "distribuicao",
        "dicionario_trechos",
    }
    assert {"nu_notific", "dt_nasc", "cs_raca", "id_municip"} <= set(COLUNAS_PROIBIDAS)


def test_nenhuma_coluna_da_demo_e_proibida():
    from fnmatch import fnmatchcase

    for tabela, (colunas, _ordem) in TABELAS_DEMO.items():
        for coluna in colunas:
            assert not any(fnmatchcase(coluna, p) for p in COLUNAS_PROIBIDAS), (tabela, coluna)
