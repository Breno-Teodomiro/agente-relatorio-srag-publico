"""Configuração única do projeto, lida das variáveis de ambiente e do `.env`."""

from pathlib import Path
from typing import Literal

from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Papel = Literal["admin", "etl", "agente", "auditor"]

USUARIOS: dict[Papel, str] = {
    "admin": "postgres",
    "etl": "srag_etl",
    "agente": "srag_agente",
    "auditor": "srag_auditor",
}


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    preco_entrada_usd_mtok: float = 0.40
    preco_saida_usd_mtok: float = 1.60

    max_chamadas_ferramenta: int = 12
    max_tokens_execucao: int = 60_000

    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

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
