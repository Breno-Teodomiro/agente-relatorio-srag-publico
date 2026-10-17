"""Dublês de teste: substituem LLM, notícias e embeddings sem custo nem rede."""

import hashlib

from srag_agente.rag.embeddings import DIMENSAO


def embedder_falso(textos: list[str]) -> list[list[float]]:
    """Vetor determinístico por texto: textos iguais → distância zero."""
    vetores = []
    for texto in textos:
        semente = hashlib.sha256(texto.encode()).digest()
        vetores.append([semente[i % len(semente)] / 255 + 0.01 for i in range(DIMENSAO)])
    return vetores


# --- Task 9: dublês das ferramentas (repositório, buscador, dicionário) ---
from datetime import date, timedelta  # noqa: E402

from srag_agente.ferramentas.dicionario import TrechoDicionario  # noqa: E402
from srag_agente.ferramentas.noticias import Noticia, ResultadoBusca  # noqa: E402
from srag_agente.metricas.repositorio import DadosIndisponiveis, Referencia  # noqa: E402

HOJE = date(2026, 10, 14)
REF = date(2025, 6, 25)

INDICADORES = {
    "total_casos": 1000,
    "obitos": 80,
    "com_desfecho": 800,
    "uti_sim": 290,
    "uti_informada": 900,
    "vacina_covid_sim": 600,
    "vacina_covid_informada": 800,
    "vacina_gripe_sim": 250,
    "vacina_gripe_informada": 780,
    "periodo_inicio": date(2025, 1, 1),
    "periodo_fim": REF,
}
JANELAS = {
    "casos_7d_atual": 84,
    "casos_7d_anterior": 70,
    "casos_30d_atual": 300,
    "casos_30d_anterior": 250,
    "casos_em_consolidacao": 150,
    "data_consolidada": date(2025, 6, 11),
}
DISTRIBUICOES = {
    "classificacao": [
        {"categoria": "influenza", "casos": 400},
        {"categoria": "covid19", "casos": 300},
        {"categoria": "outro_virus", "casos": 200},
        {"categoria": "nao_especificado", "casos": 100},
    ],
    "faixa_etaria": [
        {"categoria": "60+", "casos": 500},
        {"categoria": "<1", "casos": 300},
        {"categoria": "18-39", "casos": 200},
    ],
}


class RepositorioFalso:
    """Mesma interface do RepositorioGold. 'RR' simula UF sem casos; 'AP', UF com casos mas sem
    data de primeiros sintomas válida (sem linha em janelas_aumento e com período nulo)."""

    def referencia(self):
        return Referencia(REF, REF - timedelta(days=14), date(2025, 1, 1), 1000)

    def _checar(self, uf):
        if uf == "RR":
            raise DadosIndisponiveis(f"sem casos para a UF {uf}")

    def indicadores(self, uf):
        self._checar(uf)
        if uf == "AP":  # a gold devolve o período NULO quando nenhum caso tem data de sintoma
            return {**INDICADORES, "periodo_inicio": None, "periodo_fim": None}
        return dict(INDICADORES)

    def janelas_aumento(self, uf):
        self._checar(uf)
        if uf == "AP":
            raise DadosIndisponiveis(f"sem casos para a UF {uf}")
        return dict(JANELAS)

    def serie_diaria(self, uf):
        return [
            {"dia": REF - timedelta(days=29 - i), "casos": 10, "em_consolidacao": i >= 16}
            for i in range(30)
        ]

    def serie_mensal(self, uf):
        meses = [f"2024-{m:02d}" for m in range(7, 13)] + [f"2025-{m:02d}" for m in range(1, 7)]
        return [
            {
                "mes": mes,
                "casos": None if i < 6 else 100,
                "fora_da_base": i < 6,
                "parcial": i == 11,
            }
            for i, mes in enumerate(meses)
        ]

    def distribuicao(self, dimensao, uf):
        self._checar(uf)
        return DISTRIBUICOES[dimensao]


NOTICIAS = {
    "periodo_dos_dados": Noticia(
        titulo="Fiocruz aponta alta de SRAG por influenza",
        fonte="Fiocruz",
        url="https://portal.fiocruz.br/n1",
        data=date(2025, 6, 20),
        trecho="Boletim InfoGripe indica alta de 15% nas internações por SRAG.",
        confiavel=True,
        janela="periodo_dos_dados",
    ),
    "cenario_atual": Noticia(
        titulo="Ministério divulga boletim semanal de SRAG",
        fonte="gov.br",
        url="https://www.gov.br/saude/n2",
        data=HOJE - timedelta(days=5),
        trecho="Boletim semanal com a situação dos vírus respiratórios.",
        confiavel=True,
        janela="cenario_atual",
    ),
}


class BuscadorFalso:
    def __init__(self, vazio: bool = False):
        self.vazio = vazio
        self.chamadas: list[tuple] = []

    def buscar(self, termo, janela, inicio, fim):
        self.chamadas.append((termo, janela, inicio, fim))
        return ResultadoBusca(
            termo=termo,
            janela=janela,
            inicio=inicio,
            fim=fim,
            provedor="nenhum" if self.vazio else "tavily",
            noticias=[] if self.vazio else [NOTICIAS[janela]],
        )


class DicionarioFalso:
    def __init__(self, trechos: list[TrechoDicionario] | None = None):
        self.trechos = trechos

    def buscar(self, pergunta):
        if self.trechos is not None:
            return self.trechos
        return [
            TrechoDicionario(
                campo="UTI",
                pagina=12,
                distancia=0.1,
                texto="Campo UTI: Internado em UTI? 1-Sim 2-Não 9-Ignorado",
            )
        ]


def ferramentas_falsas(**kwargs):
    from srag_agente.ferramentas.registro import criar_ferramentas

    return criar_ferramentas(
        kwargs.get("repo", RepositorioFalso()),
        kwargs.get("buscador", BuscadorFalso()),
        kwargs.get("dicionario", DicionarioFalso()),
        hoje=lambda: HOJE,
    )


CHAMADAS_COMPLETAS = [
    ("consultar_metricas", {"metrica": "aumento_casos"}),
    ("consultar_metricas", {"metrica": "mortalidade"}),
    ("consultar_metricas", {"metrica": "uti"}),
    ("consultar_metricas", {"metrica": "vacinacao"}),
    ("consultar_serie", {"tipo": "diaria_30d"}),
    ("consultar_serie", {"tipo": "mensal_12m"}),
    ("consultar_distribuicao", {"dimensao": "classificacao"}),
    ("buscar_noticias", {"termo": "SRAG influenza", "janela": "periodo_dos_dados"}),
    ("buscar_noticias", {"termo": "SRAG", "janela": "cenario_atual"}),
]


def evidencias_completas(uf: str = "BR"):
    from srag_agente.ferramentas.registro import executar

    ferramentas = ferramentas_falsas()
    evidencias = []
    for nome, argumentos in CHAMADAS_COMPLETAS:
        if "metrica" in argumentos or "tipo" in argumentos or "dimensao" in argumentos:
            argumentos = {**argumentos, "uf": uf}
        motivo = "evidência obrigatória do relatório"
        evidencias.append(executar(ferramentas[nome], {**argumentos, "motivo": motivo}))
    return evidencias


# --- Task 11: textos do redator para os guardrails de saída ---
def texto_valido(**substituir):
    """Texto do redator coerente com `evidencias_completas()`: passa em todos os guardrails."""
    from srag_agente.modelos import ComentarioMetrica, TextoRelatorio

    campos = {
        "resumo_executivo": "Os casos subiram 20,0% na última semana consolidada, com "
        "mortalidade de 10,0% entre os casos com desfecho.",
        "comentarios_metricas": [
            ComentarioMetrica(
                metrica="aumento_casos", comentario="84 casos contra 70 na semana anterior."
            ),
            ComentarioMetrica(
                metrica="mortalidade", comentario="80 óbitos em 800 casos com desfecho."
            ),
            ComentarioMetrica(metrica="uti", comentario="32,2% dos casos foram para a UTI."),
            ComentarioMetrica(
                metrica="vacinacao",
                comentario="75,0% dos casos tinham vacina contra COVID-19 e 32,1% contra a gripe.",
            ),
        ],
        "contexto_periodo_dados": "O boletim InfoGripe apontou alta de 15% nas internações [1].",
        "cenario_atual": "O Ministério divulgou novo boletim semanal [2].",
        "limitacoes": "A taxa de UTI não mede ocupação de leitos e os últimos 14 dias ainda "
        "estão em consolidação.",
    }
    campos.update(substituir)
    return TextoRelatorio(**campos)


def texto_neutro():
    """Texto sem números nem citações: passa em qualquer cenário de evidências."""
    from srag_agente.modelos import ComentarioMetrica, TextoRelatorio

    return TextoRelatorio(
        resumo_executivo="Relatório gerado a partir das evidências disponíveis.",
        comentarios_metricas=[
            ComentarioMetrica(metrica=m, comentario="Ver o cartão.")
            for m in ("aumento_casos", "mortalidade", "uti", "vacinacao")
        ],
        contexto_periodo_dados="Nenhuma notícia foi citada nesta seção.",
        cenario_atual="Nenhuma notícia foi citada nesta seção.",
        limitacoes="A taxa de UTI não mede ocupação de leitos.",
    )
