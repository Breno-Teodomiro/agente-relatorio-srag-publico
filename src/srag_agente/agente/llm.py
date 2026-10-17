"""Fábrica de modelos (OpenRouter principal, OpenAI reserva) e chamadas estruturadas."""

import json
from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from srag_agente.agente.contexto import ContextoRedacao
from srag_agente.agente.prompts import carregar_prompt
from srag_agente.config import Config
from srag_agente.ferramentas.registro import Evidencia
from srag_agente.guardrails.conteudo_externo import formatar_para_llm, limpar_texto
from srag_agente.guardrails.entrada import ClassificacaoEscopo, Classificador
from srag_agente.modelos import TextoRelatorio

URL_OPENROUTER = "https://openrouter.ai/api/v1"
LIMITE_PERGUNTA = 500


class ConfiguracaoInvalida(RuntimeError):  # noqa: N818 (nome do contrato da Task 12)
    """Falta configuração obrigatória (ex.: nenhuma chave de LLM)."""


class ErroRedacao(RuntimeError):  # noqa: N818 (nome do contrato da Task 12)
    """O redator devolveu algo fora do formato estruturado."""


@dataclass(frozen=True)
class ModeloConfigurado:
    provedor: str
    modelo: str
    chat: BaseChatModel


Redator = Callable[[ContextoRedacao], tuple[TextoRelatorio, tuple[int, int]]]


def criar_modelos(cfg: Config) -> list[ModeloConfigurado]:
    """Principal primeiro; o outro provedor entra como reserva se tiver chave."""
    opcoes = {
        "openrouter": (cfg.openrouter_api_key, cfg.llm_modelo_openrouter, URL_OPENROUTER),
        "openai": (cfg.openai_api_key, cfg.llm_modelo_openai, None),
    }
    ordem = [cfg.llm_provider, *(p for p in opcoes if p != cfg.llm_provider)]
    modelos = []
    for provedor in ordem:
        chave, modelo, base_url = opcoes[provedor]
        if chave is None or not chave.get_secret_value():
            continue
        chat = ChatOpenAI(
            model=modelo,
            api_key=chave.get_secret_value(),
            base_url=base_url,
            temperature=cfg.llm_temperatura,
            timeout=cfg.llm_timeout_s,
            max_retries=2,
        )
        modelos.append(ModeloConfigurado(provedor, modelo, chat))
    if not modelos:
        raise ConfiguracaoInvalida("Defina OPENROUTER_API_KEY ou OPENAI_API_KEY no .env.")
    return modelos


def _com_reserva(runnables: list[Runnable]) -> Runnable:
    principal, *reservas = runnables
    return principal.with_fallbacks(reservas) if reservas else principal


def modelo_com_ferramentas(
    modelos: list[ModeloConfigurado], especificacoes: list[dict]
) -> Runnable:
    return _com_reserva([m.chat.bind_tools(especificacoes) for m in modelos])


def modelo_estruturado(modelos: list[ModeloConfigurado], esquema: type) -> Runnable:
    return _com_reserva(
        [
            m.chat.with_structured_output(esquema, method="function_calling", include_raw=True)
            for m in modelos
        ]
    )


def uso_tokens(mensagem: BaseMessage | None) -> tuple[int, int]:
    uso = getattr(mensagem, "usage_metadata", None) or {}
    return int(uso.get("input_tokens", 0)), int(uso.get("output_tokens", 0))


def estimar_custo(tokens_entrada: int, tokens_saida: int, cfg: Config) -> float:
    return (
        tokens_entrada * cfg.preco_entrada_usd_mtok + tokens_saida * cfg.preco_saida_usd_mtok
    ) / 1_000_000


def pergunta_como_dado(pergunta: str) -> str:
    """Pergunta do usuário entre marcas, sem tags próprias que fechem o bloco antes da hora."""
    return f"<pergunta_usuario>{limpar_texto(pergunta, LIMITE_PERGUNTA)}</pergunta_usuario>"


def mensagem_do_redator(contexto: ContextoRedacao) -> str:
    """Evidências em JSON; texto vindo de fora (notícias, PDF) e a pergunta entram como DADO.

    Notícia e trecho do dicionário não vão no JSON cru: passam pelos mesmos formatadores que o
    orquestrador usa (campos escapados, em uma linha, dentro de <noticia_externa>). O número do
    atributo `indice` é o `id` da notícia (a numeração é sequencial).
    """
    base = contexto.model_dump(
        mode="json", exclude={"noticias", "trechos_dicionario", "pergunta_extra"}
    )
    partes = [json.dumps(base, ensure_ascii=False)]
    if contexto.noticias:
        partes.append(formatar_para_llm([n.model_dump() for n in contexto.noticias]))
    else:
        partes.append("Nenhuma notícia disponível em nenhuma janela.")
    if contexto.trechos_dicionario:
        dicionario = Evidencia(
            ferramenta="consultar_dicionario",
            argumentos={},
            dados={"pergunta": "dicionário de dados", "trechos": contexto.trechos_dicionario},
        )
        partes.append(dicionario.para_llm())
    if contexto.pergunta_extra:
        partes.append(pergunta_como_dado(contexto.pergunta_extra))
    return "\n".join(partes)


def _extrair(saida: dict) -> tuple[object, tuple[int, int]]:
    if saida.get("parsed") is None:
        raise ErroRedacao(str(saida.get("parsing_error") or "saída vazia"))
    return saida["parsed"], uso_tokens(saida.get("raw"))


def criar_redator(estruturado: Runnable) -> Redator:
    def redigir(contexto: ContextoRedacao) -> tuple[TextoRelatorio, tuple[int, int]]:
        saida = estruturado.invoke(
            [SystemMessage(carregar_prompt("redator")), HumanMessage(mensagem_do_redator(contexto))]
        )
        return _extrair(saida)

    return redigir


def criar_classificador(estruturado: Runnable) -> Classificador:
    def classificar(pergunta: str) -> tuple[ClassificacaoEscopo, tuple[int, int]]:
        saida = estruturado.invoke(
            [
                SystemMessage(carregar_prompt("classificador")),
                HumanMessage(pergunta_como_dado(pergunta)),
            ]
        )
        return _extrair(saida)

    return classificar
