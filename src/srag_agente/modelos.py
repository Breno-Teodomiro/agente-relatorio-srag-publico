"""Modelos compartilhados por agente, guardrails e relatório."""

from datetime import date

from pydantic import BaseModel, Field, ValidationError, model_validator

from srag_agente.ferramentas.noticias import Janela
from srag_agente.ferramentas.registro import Evidencia
from srag_agente.guardrails.conteudo_externo import url_segura
from srag_agente.metricas.catalogo import ChaveMetrica


class ComentarioMetrica(BaseModel):
    metrica: ChaveMetrica
    comentario: str = Field(max_length=700)


class TextoRelatorio(BaseModel):
    """O que o LLM redator devolve. Números dos cartões e gráficos NÃO vêm daqui."""

    resumo_executivo: str = Field(max_length=1500, description="3 a 5 frases sobre o cenário.")
    comentarios_metricas: list[ComentarioMetrica] = Field(
        description="Um comentário curto para cada uma das 4 métricas."
    )
    contexto_periodo_dados: str = Field(
        max_length=2000,
        description="Explica os números com notícias da janela periodo_dos_dados, citando [n].",
    )
    cenario_atual: str = Field(
        max_length=2000, description="Cenário de hoje com notícias da janela cenario_atual, [n]."
    )
    resposta_pergunta_extra: str | None = Field(default=None, max_length=1500)
    limitacoes: str = Field(max_length=1500, description="Limitações dos dados e das métricas.")

    @model_validator(mode="after")
    def _um_comentario_por_metrica(self) -> "TextoRelatorio":
        # Com dois comentários da mesma métrica, `secoes()` guardaria só um: o outro seria
        # publicado sem passar pelo guardrail de saída.
        metricas = [c.metrica for c in self.comentarios_metricas]
        if repetidas := sorted({m for m in metricas if metricas.count(m) > 1}):
            raise ValueError(f"comentário repetido para a métrica: {', '.join(repetidas)}")
        return self

    def secoes(self) -> dict[str, str]:
        secoes = {
            "resumo_executivo": self.resumo_executivo,
            "contexto_periodo_dados": self.contexto_periodo_dados,
            "cenario_atual": self.cenario_atual,
            "limitacoes": self.limitacoes,
        }
        for comentario in self.comentarios_metricas:
            secoes[f"comentario_{comentario.metrica}"] = comentario.comentario
        if self.resposta_pergunta_extra:
            secoes["resposta_pergunta_extra"] = self.resposta_pergunta_extra
        return secoes

    def com_secoes(self, secoes: dict[str, str]) -> "TextoRelatorio":
        """Cópia com o texto das seções trocado (ex.: mascarado). Não revalida o tamanho: a
        máscara "[dado removido]" pode ser maior que o dado que ela esconde."""
        return self.model_copy(
            update={
                "resumo_executivo": secoes["resumo_executivo"],
                "contexto_periodo_dados": secoes["contexto_periodo_dados"],
                "cenario_atual": secoes["cenario_atual"],
                "limitacoes": secoes["limitacoes"],
                "resposta_pergunta_extra": secoes.get(
                    "resposta_pergunta_extra", self.resposta_pergunta_extra
                ),
                "comentarios_metricas": [
                    c.model_copy(update={"comentario": secoes[f"comentario_{c.metrica}"]})
                    for c in self.comentarios_metricas
                ],
            }
        )


class NoticiaCitavel(BaseModel):
    id: int
    titulo: str
    fonte: str
    url: str
    data: str | None
    janela: Janela
    confiavel: bool
    trecho: str
    # Intervalo da busca que devolveu a notícia: o guardrail de saída confere a data contra ele.
    inicio_janela: date | None = None
    fim_janela: date | None = None


def _data_ou_none(valor: object) -> date | None:
    try:
        return date.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return None


def numerar_noticias(evidencias: list[Evidencia]) -> list[NoticiaCitavel]:
    """Numera as notícias de todas as buscas (1, 2, 3…), sem repetir URL.

    A janela que vale é a da busca que rodou (não a que o item declara). Item com URL insegura
    ou malformado não ganha número: não pode ser citado nem virar link no relatório.
    """
    vistas: set[str] = set()
    citaveis: list[NoticiaCitavel] = []
    for evidencia in evidencias:
        if evidencia.ferramenta != "buscar_noticias":
            continue
        dados = evidencia.dados
        for noticia in dados.get("noticias") or []:
            url = noticia.get("url") if isinstance(noticia, dict) else None
            if not isinstance(url, str) or url in vistas or not url_segura(url):
                continue
            try:
                citavel = NoticiaCitavel(
                    id=len(citaveis) + 1,
                    titulo=noticia["titulo"],
                    fonte=noticia["fonte"],
                    url=url,
                    data=None if noticia.get("data") is None else str(noticia["data"]),
                    janela=dados["janela"],
                    confiavel=noticia["confiavel"],
                    trecho=noticia["trecho"],
                    inicio_janela=_data_ou_none(dados.get("inicio")),
                    fim_janela=_data_ou_none(dados.get("fim")),
                )
            except (KeyError, ValidationError):
                continue
            vistas.add(url)
            citaveis.append(citavel)
    return citaveis
