"""Dublês de teste: substituem LLM, notícias e embeddings sem custo nem rede."""

import hashlib
from datetime import date, timedelta

from srag_agente.ferramentas.dicionario import TrechoDicionario
from srag_agente.ferramentas.noticias import Noticia, ResultadoBusca
from srag_agente.metricas.repositorio import DadosIndisponiveis, Referencia
from srag_agente.rag.embeddings import DIMENSAO


def embedder_falso(textos: list[str]) -> list[list[float]]:
    """Vetor determinístico por texto: textos iguais → distância zero."""
    vetores = []
    for texto in textos:
        semente = hashlib.sha256(texto.encode()).digest()
        vetores.append([semente[i % len(semente)] / 255 + 0.01 for i in range(DIMENSAO)])
    return vetores


# --- dublês das ferramentas (repositório, buscador, dicionário) ---
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


NOTICIA_SEM_DATA = NOTICIAS["cenario_atual"].model_copy(update={"data": None})


class BuscadorSemData(BuscadorFalso):
    """A fonte não informou a data da notícia do cenário atual (aceita em qualquer janela)."""

    def buscar(self, termo, janela, inicio, fim):
        resultado = super().buscar(termo, janela, inicio, fim)
        if janela == "cenario_atual":
            resultado.noticias = [NOTICIA_SEM_DATA]
        return resultado


# --- I2: troca silenciosa do modelo principal pela reserva ---
MODELO_RESERVA = "gpt-4.1-mini-reserva"


def _principal_fora_do_ar(_mensagens):
    raise TimeoutError("principal fora do ar")


def resposta_crua(modelo: str | None = MODELO_RESERVA, content: str = "", **campos):
    """AIMessage como a do langchain_openai: o modelo que respondeu vem em `model_name`."""
    from langchain_core.messages import AIMessage

    metadados = {"model_name": modelo} if modelo is not None else {}
    uso = {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}
    return AIMessage(content=content, response_metadata=metadados, usage_metadata=uso, **campos)


def estruturado_com_reserva(parsed, modelo: str | None = MODELO_RESERVA):
    """Mesmo encadeamento de `modelo_estruturado` (include_raw + with_fallbacks): o principal
    levanta erro e a reserva responde."""
    from langchain_core.runnables import RunnableLambda

    def reserva(_mensagens):
        return {"raw": resposta_crua(modelo), "parsed": parsed, "parsing_error": None}

    return RunnableLambda(_principal_fora_do_ar).with_fallbacks([RunnableLambda(reserva)])


# --- revisão final: saída mal formada traz o texto cru do modelo na mensagem de erro (I2) ---
# É o formato do OutputParserException do langchain com method="function_calling": os
# `arguments` crus (o que o modelo escreveu) vêm dentro da mensagem.
TEXTO_CRU_DO_MODELO = "TEXTO CRU DO MODELO com CPF 123.456.789-09"
MENSAGEM_ERRO_CRUA = (
    f'Function TextoRelatorio arguments:\n\n{{"resumo_executivo": "{TEXTO_CRU_DO_MODELO}", '
    '"limitacoes": "corta\n\nare not valid JSON. Received JSONDecodeError Unterminated string'
)


def estruturado_mal_formado(raw=None):
    """`with_structured_output(include_raw=True)` que não conseguiu ler a resposta do modelo.

    A resposta crua (com uso de tokens 50/10, de `resposta_crua`) vem em `raw`, como no real.
    """
    from langchain_core.exceptions import OutputParserException
    from langchain_core.runnables import RunnableLambda

    bruto = resposta_crua() if raw is None else raw
    return RunnableLambda(
        lambda _: {
            "raw": bruto,
            "parsed": None,
            "parsing_error": OutputParserException(MENSAGEM_ERRO_CRUA),
        }
    )


def orquestrador_com_reserva(*respostas):
    """Orquestrador cujo principal falha sempre; a reserva devolve `respostas` em ordem."""
    from langchain_core.runnables import RunnableLambda

    fila = iter(respostas)
    return RunnableLambda(_principal_fora_do_ar).with_fallbacks(
        [RunnableLambda(lambda _mensagens: next(fila))]
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
