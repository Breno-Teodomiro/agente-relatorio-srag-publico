.PHONY: instalar subir parar carga indexar qualidade relatorio testes lint diagrama tudo

instalar:
	uv sync --all-extras
	uv run pre-commit install

subir:
	docker compose up -d --wait banco

parar:
	docker compose down

carga:
	uv run python -m srag_agente.dados

testes:
	EXIGIR_BANCO=1 uv run pytest --cov=srag_agente --cov-report=term-missing

lint:
	uv run ruff check . && uv run ruff format --check .
