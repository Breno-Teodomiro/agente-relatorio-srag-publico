.PHONY: instalar subir parar carga indexar avaliar-rag qualidade grafo relatorio testes lint \
        diagrama docker-build docker-carga docker-indexar docker-relatorio tudo

UF ?= BR
# Pergunta complementar opcional: make relatorio UF=SP PERGUNTA="Como está a UTI?"
# Vai ao comando pelo ambiente ("$$PERGUNTA"), nunca colada no texto do comando: aspas ou $ na
# pergunta não viram código do shell. Sem PERGUNTA, o --pergunta nem aparece.
# $(value) guarda o texto literal: sem ele o make leria "R$ 100" como variável e comeria o "$ ".
override PERGUNTA := $(value PERGUNTA)
export PERGUNTA
COM_PERGUNTA = $${PERGUNTA:+--pergunta "$$PERGUNTA"}
# No WSL sem integração do Docker Desktop o binário é docker.exe: make subir DOCKER=docker.exe
DOCKER ?= docker
# Versão gravada na auditoria de quem roda pela imagem (no container não há Git). Vai como
# --build-arg, e não como variável de ambiente: o docker.exe não enxerga o ambiente do WSL.
VERSAO_CODIGO = $(shell git rev-parse --short HEAD 2>/dev/null || echo desconhecida)$(shell \
  test -n "$$(git status --porcelain 2>/dev/null)" && echo -modificado)

instalar:
	uv sync --all-extras
	uv run pre-commit install

subir:
	$(DOCKER) compose up -d --wait banco

parar:
	$(DOCKER) compose down

# Carga atômica: raw + silver + gold juntas (recarregar só a raw deixaria a silver velha).
carga:
	uv run srag carga

indexar:
	uv run srag indexar

# Rodar logo depois do `make indexar`, nunca depois do pytest (os testes reindexam o srag_teste).
avaliar-rag:
	uv run python -m srag_agente.rag.avaliar

qualidade:
	uv run srag qualidade

grafo:
	uv run srag grafo

relatorio:
	uv run srag relatorio --uf $(UF) $(COM_PERGUNTA)

testes:
	EXIGIR_BANCO=1 uv run pytest --cov=srag_agente --cov-report=term-missing

lint:
	uv run ruff check . && uv run ruff format --check .

# Pastas de saída criadas aqui: se não existirem, o daemon as cria como root (Linux nativo) e o
# usuário `srag` da imagem não consegue escrever nelas.
PASTAS_SAIDA = relatorios .cache

docker-build:
	mkdir -p $(PASTAS_SAIDA)
	$(DOCKER) compose --profile app build --build-arg SRAG_VERSAO_CODIGO=$(VERSAO_CODIGO) app

docker-carga:
	mkdir -p $(PASTAS_SAIDA)
	$(DOCKER) compose --profile app run --rm app carga

docker-indexar:
	mkdir -p $(PASTAS_SAIDA)
	$(DOCKER) compose --profile app run --rm app indexar

docker-relatorio:
	mkdir -p $(PASTAS_SAIDA)
	$(DOCKER) compose --profile app run --rm app relatorio --uf $(UF) $(COM_PERGUNTA)

tudo: subir carga indexar relatorio

# Renderiza com mermaid-cli (npx + Chromium). Sem Chromium do sistema, o puppeteer baixa o dele;
# para usar um já instalado: PUPPETEER_EXECUTABLE_PATH=/caminho/do/chrome make diagrama
diagrama:
	npx -y @mermaid-js/mermaid-cli@11.4.2 -p docs/puppeteer.config.json -w 1800 \
	  -i docs/arquitetura.mmd -o docs/arquitetura.pdf -f -b white
