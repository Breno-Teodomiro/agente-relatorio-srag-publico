"""Demonstração online: constantes de contrato entre o pacote, a carga, o teto e a tela.

Ficam aqui, num lugar só, para que exportação, carga, conferência e testes leiam a mesma lista.
"""

# tabela: (colunas permitidas, ORDER BY determinístico). Coluna nova na gold não vai para a
# nuvem sem decisão: a exportação compara com esta lista e falha se aparecer uma que não consta.
TABELAS_DEMO: dict[str, tuple[tuple[str, ...], str]] = {
    "referencia": (("data_referencia", "data_consolidada", "inicio_base", "total_casos"), "1"),
    "casos_diarios": (("uf", "dia", "casos"), "uf, dia"),
    "indicadores": (
        (
            "uf",
            "total_casos",
            "obitos",
            "com_desfecho",
            "uti_sim",
            "uti_informada",
            "vacina_covid_sim",
            "vacina_covid_informada",
            "vacina_gripe_sim",
            "vacina_gripe_informada",
            "periodo_inicio",
            "periodo_fim",
        ),
        "uf",
    ),
    "janelas_aumento": (
        (
            "uf",
            "casos_7d_atual",
            "casos_7d_anterior",
            "casos_30d_atual",
            "casos_30d_anterior",
            "casos_em_consolidacao",
            "data_consolidada",
        ),
        "uf",
    ),
    "distribuicao": (("dimensao", "uf", "categoria", "casos"), "dimensao, uf, categoria"),
    "dicionario_trechos": (("id", "campo", "pagina", "texto", "embedding"), "id"),
}

# Nomes (minúsculos) que nunca entram no pacote; `*` é curinga no estilo fnmatch. É a regra do
# ADR 0004: identificadores, município, raça/etnia, nascimento e texto livre ficam na `raw`.
COLUNAS_PROIBIDAS: tuple[str, ...] = (
    "nu_notific",
    "dt_nasc",
    "id_municip",
    "co_mun_not",
    "co_mun_res",
    "id_mn_resi",
    "cs_raca",
    "cs_sexo",
    "nm_*",
    "obs*",
    "id_caso",
    "*_out",
)

# SQLSTATE próprio do gatilho do teto diário (classe `SR`, que o Postgres não usa).
SQLSTATE_TETO = "SR429"
# Trava consultiva do gatilho, vizinha da trava da transformação (7350301).
TRAVA_TETO = 7_350_302
FUSO_DEMO = "America/Sao_Paulo"

# Hosts que são a máquina ou o Compose locais: o modo demo e a carga os recusam como alvo.
HOSTS_LOCAIS: frozenset[str] = frozenset(
    {"localhost", "127.0.0.1", "::1", "banco", "host.docker.internal"}
)
# Modos de TLS aceitos em banco remoto (sem `disable`, `allow` nem `prefer`).
SSLMODES_SEGUROS: frozenset[str] = frozenset({"require", "verify-ca", "verify-full"})
