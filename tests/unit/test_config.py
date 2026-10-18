from pathlib import Path

import pytest
from pydantic import ValidationError

from srag_agente.config import RAIZ_PROJETO, Config, raiz_do_projeto

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
