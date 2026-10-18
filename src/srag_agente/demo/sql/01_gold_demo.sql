-- Gold da demonstração: tabelas com os MESMOS nomes e colunas das views locais (o código que lê a
-- gold não muda uma linha). O conteúdo chega pelo COPY da carga, nunca por este arquivo.
-- As chaves primárias são conferência de integridade: linha duplicada por UF e dia faz a carga
-- parar em vez de publicar número dobrado.
CREATE TABLE IF NOT EXISTS gold.referencia (
  data_referencia date,
  data_consolidada date,
  inicio_base date,
  total_casos integer NOT NULL CHECK (total_casos >= 0)
);

CREATE TABLE IF NOT EXISTS gold.casos_diarios (
  uf text NOT NULL,
  dia date NOT NULL,
  casos integer NOT NULL CHECK (casos >= 0),
  PRIMARY KEY (uf, dia)
);

CREATE TABLE IF NOT EXISTS gold.indicadores (
  uf text PRIMARY KEY,
  total_casos integer NOT NULL,
  obitos integer NOT NULL,
  com_desfecho integer NOT NULL,
  uti_sim integer NOT NULL,
  uti_informada integer NOT NULL,
  vacina_covid_sim integer NOT NULL,
  vacina_covid_informada integer NOT NULL,
  vacina_gripe_sim integer NOT NULL,
  vacina_gripe_informada integer NOT NULL,
  periodo_inicio date,
  periodo_fim date
);

CREATE TABLE IF NOT EXISTS gold.janelas_aumento (
  uf text PRIMARY KEY,
  casos_7d_atual integer NOT NULL,
  casos_7d_anterior integer NOT NULL,
  casos_30d_atual integer NOT NULL,
  casos_30d_anterior integer NOT NULL,
  casos_em_consolidacao integer NOT NULL,
  data_consolidada date
);

CREATE TABLE IF NOT EXISTS gold.distribuicao (
  dimensao text NOT NULL,
  uf text NOT NULL,
  categoria text NOT NULL,
  casos integer NOT NULL CHECK (casos >= 0),
  PRIMARY KEY (dimensao, uf, categoria)
);

-- Texto do dicionário de dados (documento público do DATASUS) com o vetor de cada trecho.
CREATE TABLE IF NOT EXISTS gold.dicionario_trechos (
  id integer PRIMARY KEY,
  campo text,
  pagina integer NOT NULL,
  texto text NOT NULL,
  embedding vector(384) NOT NULL
);

-- Proveniência: cada pacote carregado fica registrado (nada se apaga). A tela mostra o último.
CREATE TABLE IF NOT EXISTS gold.pacote_demo (
  sha256 text NOT NULL,
  gerado_em timestamptz NOT NULL,
  versao_codigo text NOT NULL,
  data_referencia date,
  carregado_em timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (sha256, carregado_em)
);

-- Só leitura, só para o agente; o dono escreve. A conferência da carga recusa objeto a mais na
-- gold, então o GRANT abaixo nunca alcança tabela que não seja da lista.
REVOKE ALL ON ALL TABLES IN SCHEMA gold FROM PUBLIC;
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO srag_agente;
