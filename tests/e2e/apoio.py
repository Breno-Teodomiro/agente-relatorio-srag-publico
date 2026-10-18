"""Montagem comum dos E2E: núcleo real, Postgres `srag_teste`, LLM e notícias falsos.

Nada aqui chama a rede nem gasta crédito. Reaproveita os dublês de `tests/fakes.py` e as
fixtures do banco de `tests/conftest.py`; o que é novo é só a cola: um roteiro de LLM que
acompanha a UF, um redator que escreve a partir das evidências (como o real faria) e os leitores
dos arquivos e da trilha gravada.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda

from srag_agente.agente.contexto import ContextoRedacao
from srag_agente.config import Config
from srag_agente.guardrails.entrada import ClassificacaoEscopo, Classificador
from srag_agente.modelos import ComentarioMetrica, TextoRelatorio
from srag_agente.nucleo import Componentes, ResultadoExecucao, gerar_relatorio
from tests.fakes import CHAMADAS_COMPLETAS, HOJE, BuscadorFalso, embedder_falso

USO = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
MOTIVO = "evidência obrigatória do relatório"
CHAMADAS_PI = [*CHAMADAS_COMPLETAS, ("consultar_distribuicao", {"dimensao": "faixa_etaria"})]


def chamadas_para(uf: str, lista=CHAMADAS_COMPLETAS) -> list[dict]:
    """Chamadas de ferramenta do orquestrador falso, na UF pedida (notícia não tem UF)."""
    return [
        {
            "name": nome,
            "args": {**args, **({} if nome == "buscar_noticias" else {"uf": uf}), "motivo": MOTIVO},
            "id": f"c{i}",
        }
        for i, (nome, args) in enumerate(lista)
    ]


def _pct(fracao: float) -> str:
    return f"{fracao * 100:.1f}".replace(".", ",") + "%"


def texto_dos_dados(contexto: ContextoRedacao, **substituir) -> TextoRelatorio:
    """O texto que um redator bem-comportado escreveria a partir das evidências recebidas.

    Os números saem do contexto (ou seja, do SQL); as notícias são citadas pela janela certa.
    `substituir` troca seções inteiras para montar um texto adversarial sobre esta base.
    """
    m = {metrica["chave"]: metrica for metrica in contexto.metricas}
    aumento, mortalidade, uti, vacinacao = (m[c] for c in ("aumento_casos", "mortalidade", "uti",
                                                           "vacinacao"))  # fmt: skip
    ids = {n.janela: n.id for n in contexto.noticias}
    sentido = "subiram" if aumento["valor"] >= 0 else "caíram"
    campos = {
        "resumo_executivo": f"Os casos {sentido} {_pct(abs(aumento['valor']))} na última semana "
        f"consolidada, com mortalidade de {_pct(mortalidade['valor'])} entre os casos com "
        "desfecho.",
        "comentarios_metricas": [
            ComentarioMetrica(
                metrica="aumento_casos",
                comentario=f"{aumento['numerador']} casos contra {aumento['denominador']} na "
                "semana anterior.",
            ),
            ComentarioMetrica(
                metrica="mortalidade",
                comentario=f"{mortalidade['numerador']} óbitos em {mortalidade['denominador']} "
                "casos com desfecho.",
            ),
            ComentarioMetrica(
                metrica="uti",
                comentario=f"{_pct(uti['valor'])} dos casos com a informação foram para a UTI.",
            ),
            ComentarioMetrica(
                metrica="vacinacao",
                comentario=f"{_pct(vacinacao['valor'])} dos casos tinham vacina contra COVID-19 e "
                f"{_pct(vacinacao['complemento']['vacina_gripe'])} contra a gripe.",
            ),
        ],
        "contexto_periodo_dados": "Boletim de acompanhamento de SRAG no período dos dados"
        + (f" [{ids['periodo_dos_dados']}]." if "periodo_dos_dados" in ids else "."),
        "cenario_atual": "Boletim semanal sobre vírus respiratórios"
        + (f" [{ids['cenario_atual']}]." if "cenario_atual" in ids else "."),
        "limitacoes": "A taxa de UTI não mede ocupação de leitos e os últimos 14 dias ainda "
        "estão em consolidação.",
    }
    if contexto.pergunta_extra:
        campos["resposta_pergunta_extra"] = (
            f"A taxa de internação em UTI está em {_pct(uti['valor'])}, sobre os casos com a "
            "informação preenchida."
        )
    campos.update(substituir)
    return TextoRelatorio(**campos)


def classificacao(no_escopo: bool = True, motivo: str = "sobre a situação de SRAG"):
    return ClassificacaoEscopo(no_escopo=no_escopo, motivo=motivo)


@dataclass
class Simulacao:
    """Uma execução completa do núcleo com LLM e notícias falsos, e o que ela observou.

    `textos`: o redator usa um por tentativa (o último se repete); cada item é um texto pronto
    ou uma função do contexto. As listas `contextos`, `entradas_orquestrador` e
    `perguntas_classificadas` guardam o que cada LLM falso recebeu, para provar o que NÃO chegou.
    """

    uf: str = "BR"
    pergunta: str | None = None
    textos: Sequence[TextoRelatorio | Callable[[ContextoRedacao], TextoRelatorio]] = (
        texto_dos_dados,
    )
    no_escopo: bool = True
    motivo_classificador: str = "sobre a situação de SRAG"
    buscador: BuscadorFalso = field(default_factory=BuscadorFalso)
    chamadas: list[tuple] = field(default_factory=lambda: list(CHAMADAS_COMPLETAS))
    orquestrador: Runnable | None = None  # troca o roteiro (ex.: principal fora do ar)
    redator: Callable | None = None  # troca o redator inteiro (ex.: com reserva)
    classificador: Classificador | None = None
    contextos: list[ContextoRedacao] = field(default_factory=list)
    entradas_orquestrador: list[list] = field(default_factory=list)
    perguntas_classificadas: list[str] = field(default_factory=list)
    resultado: ResultadoExecucao | None = None

    def _orquestrador(self) -> Runnable:
        if self.orquestrador is not None:
            return self.orquestrador
        modelo = GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=chamadas_para(self.uf, self.chamadas),
                        usage_metadata=USO,
                    ),
                    AIMessage(content="EVIDENCIAS_COMPLETAS", usage_metadata=USO),
                ]
            )
        )

        def invocar(mensagens):
            self.entradas_orquestrador.append(list(mensagens))
            return modelo.invoke(mensagens)

        return RunnableLambda(invocar)

    def _redator(self, contexto: ContextoRedacao):
        self.contextos.append(contexto)
        texto = self.textos[min(len(self.contextos) - 1, len(self.textos) - 1)]
        return (texto(contexto) if callable(texto) else texto), (200, 80), None

    def _classificador(self, texto: str):
        self.perguntas_classificadas.append(texto)
        return classificacao(self.no_escopo, self.motivo_classificador), (10, 2), None

    def componentes(self) -> Componentes:
        return Componentes(
            provedor="falso",
            modelo="falso",
            modelo_orquestrador=self._orquestrador(),
            redator=self.redator or self._redator,
            classificador=self.classificador or self._classificador,
            buscador=self.buscador,
            embedder=embedder_falso,
        )

    def rodar(self, cfg: Config, pasta: Path) -> ResultadoExecucao:
        cfg = cfg.model_copy(update={"pasta_relatorios": pasta})
        self.resultado = gerar_relatorio(
            cfg, uf=self.uf, pergunta_extra=self.pergunta, hoje=HOJE,
            componentes=self.componentes(),
        )  # fmt: skip
        return self.resultado

    @property
    def html(self) -> str:
        return self.resultado.arquivos.html.read_text(encoding="utf-8")

    @property
    def markdown(self) -> str:
        return self.resultado.arquivos.markdown.read_text(encoding="utf-8")


# --- leitores do que a execução deixou no disco e no banco --------------------------------------
def arquivos_gerados(pasta: Path) -> dict[str, bytes]:
    """Todo arquivo da pasta do relatório (HTML, Markdown, JSONL e PNG), em bytes."""
    return {
        p.relative_to(pasta).as_posix(): p.read_bytes() for p in pasta.rglob("*") if p.is_file()
    }


def trilha_no_banco(conn: psycopg.Connection, id_execucao) -> str:
    """A execução e todos os eventos dela, como texto JSON, tal como ficaram nas tabelas."""
    conn.commit()  # nova transação: enxerga o que o auditor gravou
    linhas = conn.execute(
        "SELECT row_to_json(e)::text FROM auditoria.execucoes AS e WHERE id_execucao = %s "
        "UNION ALL "
        "SELECT row_to_json(v)::text FROM auditoria.eventos AS v WHERE id_execucao = %s",
        (id_execucao, id_execucao),
    ).fetchall()
    return "\n".join(linha[0] for linha in linhas)


def eventos_no_banco(conn: psycopg.Connection, id_execucao) -> list[dict]:
    conn.commit()
    colunas = ("sequencia", "no", "tipo", "ferramenta", "motivo", "resultado", "erro")
    linhas = conn.execute(
        f"SELECT {', '.join(colunas)} FROM auditoria.eventos WHERE id_execucao = %s "
        "ORDER BY sequencia",
        (id_execucao,),
    ).fetchall()
    return [dict(zip(colunas, linha, strict=True)) for linha in linhas]


def ultima_execucao(conn: psycopg.Connection):
    conn.commit()
    return conn.execute("SELECT max(inicio) FROM auditoria.execucoes").fetchone()[0]


def execucao_depois_de(conn: psycopg.Connection, antes) -> dict:
    """A execução aberta depois de `antes` (para os casos em que o núcleo levanta erro)."""
    conn.commit()
    status, fim, tokens_entrada, id_execucao = conn.execute(
        "SELECT status, fim, tokens_entrada, id_execucao FROM auditoria.execucoes "
        "WHERE (%s::timestamptz IS NULL OR inicio > %s) ORDER BY inicio DESC LIMIT 1",
        (antes, antes),
    ).fetchone()
    erros = [
        e[0]
        for e in conn.execute(
            "SELECT erro FROM auditoria.eventos WHERE id_execucao = %s AND erro IS NOT NULL",
            (id_execucao,),
        ).fetchall()
    ]
    return {"status": status, "fim": fim, "tokens_entrada": tokens_entrada, "erros": erros,
            "id_execucao": id_execucao}  # fmt: skip


def jsonl(caminho: Path) -> list[dict]:
    return [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines()]
