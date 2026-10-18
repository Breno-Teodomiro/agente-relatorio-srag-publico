# Imagem da aplicação (CLI `srag`). O banco é o serviço `banco` do docker-compose.yml.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# Dependências primeiro (camada reaproveitada enquanto o uv.lock não muda), depois o código.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
# Sem Git no container: a versão gravada na auditoria vem do build (make docker-build).
ARG SRAG_VERSAO_CODIGO=desconhecida
ENV SRAG_VERSAO_CODIGO=${SRAG_VERSAO_CODIGO} PATH="/app/.venv/bin:$PATH"
# Usuário sem privilégio. O código e a .venv ficam do root (só leitura para a aplicação); o
# usuário escreve só onde precisa, as pastas que o compose monta. Um `chown -R /app` copiaria a
# .venv inteira para uma camada nova.
RUN useradd --create-home srag \
 && mkdir -p relatorios docs .cache \
 && chown srag relatorios docs .cache
USER srag
ENTRYPOINT ["srag"]
CMD ["--help"]
