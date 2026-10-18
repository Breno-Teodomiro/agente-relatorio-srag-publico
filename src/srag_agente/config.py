"""Configuração única do projeto, lida das variáveis de ambiente e do `.env`.

Nada aqui depende da pasta de onde a CLI é chamada: o `.env` e os caminhos relativos
(dados, relatórios, cache) são os da raiz do projeto.
"""

from pathlib import Path
from typing import Literal

from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Papel = Literal["admin", "etl", "agente", "auditor"]

USUARIOS: dict[Papel, str] = {
    "admin": "postgres",
    "etl": "srag_etl",
    "agente": "srag_agente",
    "auditor": "srag_auditor",
}


def raiz_do_projeto(inicio: Path, limite: Path | None = None) -> Path:
    """Primeira pasta, de `inicio` para cima, com `pyproject.toml` (para em `limite`).

    Com o pacote instalado fora do repositório (site-packages) não há pyproject acima dele:
    vale a pasta atual. No Docker a imagem copia o pyproject para /app, a raiz de lá.
    """
    for pasta in (inicio, *inicio.parents):
        if (pasta / "pyproject.toml").is_file():
            return pasta
        if pasta == limite:
            break
    return Path.cwd()


RAIZ_PROJETO = raiz_do_projeto(Path(__file__).resolve().parent)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=RAIZ_PROJETO / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Sem isto, o erro de validação repete o que recebeu (input_value={'pg_senha_admin':
        # 'segre...'}) e a senha ou a chave aparecem na tela e no log.
        hide_input_in_errors=True,
    )

    pg_host: str = "localhost"
    pg_porta: int = 5435
    pg_banco: str = "srag"
    pg_senha_admin: SecretStr
    pg_senha_etl: SecretStr
    pg_senha_agente: SecretStr
    pg_senha_auditor: SecretStr

    arquivo_dados: Path = Path("dados/brutos/INFLUD25_DATASUS-Versao26-06-2025.zip")
    arquivo_dicionario: Path = Path("dados/referencia/Dicionario_de_Dados_SRAG_Hospitalizado.pdf")
    pasta_relatorios: Path = Path("relatorios")
    pasta_cache_modelos: Path = Path(".cache/modelos")

    llm_provider: Literal["openrouter", "openai"] = "openrouter"
    llm_modelo_openrouter: str = "openai/gpt-4.1-mini"
    llm_modelo_openai: str = "gpt-4.1-mini"
    llm_temperatura: float = 0.2
    llm_timeout_s: int = 60
    openrouter_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    # Preço zero vale (modelo gratuito); negativo, nan ou infinito estragariam o custo auditado.
    preco_entrada_usd_mtok: float = Field(0.40, ge=0, allow_inf_nan=False)
    preco_saida_usd_mtok: float = Field(1.60, ge=0, allow_inf_nan=False)

    # Tetos de uma execução (guardrails.limites.Limites; mesmos padrões do spec §6). Teto zero,
    # negativo ou nan desligaria o guardrail sem aviso: é erro de configuração. A exceção é
    # max_voltas_cobertura, em que zero quer dizer "não insiste" (o Limites aceita).
    max_chamadas_ferramenta: int = Field(12, gt=0)
    max_tokens_execucao: int = Field(60_000, gt=0)
    max_segundos_execucao: float = Field(300.0, gt=0, allow_inf_nan=False)
    max_voltas_cobertura: int = Field(2, ge=0)
    max_tentativas_redacao: int = Field(2, gt=0)

    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    @field_validator(
        "arquivo_dados", "arquivo_dicionario", "pasta_relatorios", "pasta_cache_modelos"
    )
    @classmethod
    def _da_raiz(cls, caminho: Path) -> Path:
        return caminho if caminho.is_absolute() else RAIZ_PROJETO / caminho

    def _senha(self, papel: Papel) -> str:
        senhas = {
            "admin": self.pg_senha_admin,
            "etl": self.pg_senha_etl,
            "agente": self.pg_senha_agente,
            "auditor": self.pg_senha_auditor,
        }
        return senhas[papel].get_secret_value()

    def dsn(self, papel: Papel, banco: str | None = None) -> str:
        return make_conninfo(
            host=self.pg_host,
            port=self.pg_porta,
            dbname=banco or self.pg_banco,
            user=USUARIOS[papel],
            password=self._senha(papel),
        )
