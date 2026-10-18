"""Ponto único de geração do relatório (CLI hoje; uma tela chamaria o mesmo).

Regras do encerramento, valendo em sucesso e em erro:
- toda execução aberta se encerra (status final + consumo + contagem da silver que ela viu),
  inclusive com Ctrl+C; se nem a auditoria conseguir encerrar, o erro ORIGINAL é o que sobe,
  com a falha da auditoria anotada nele e no log;
- a execução só se encerra como sucesso depois que o relatório está no disco (falha ao gravá-lo
  encerra como "erro"); o arquivo da trilha vem por último, já com o encerramento;
- os tokens gastos antes de um erro entram no custo (somados da trilha de eventos);
- nada chega à auditoria sem mascarar: a pergunta nos parâmetros, o texto do erro (o registro
  mascara) e, num erro de validação do pydantic, só campo e mensagem, nunca o valor recebido.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, get_args
from uuid import UUID

import httpx
from langchain_core.runnables import Runnable

from srag_agente.agente.grafo import executar_agente
from srag_agente.agente.llm import (
    Redator,
    criar_classificador,
    criar_modelos,
    criar_redator,
    estimar_custo,
    modelo_com_ferramentas,
    modelo_estruturado,
)
from srag_agente.agente.nos import Dependencias
from srag_agente.agente.prompts import hash_prompts
from srag_agente.auditoria.registro import (
    STATUS_FINAIS,
    Auditoria,
    AuditoriaPostgres,
    descrever_erro,
    sem_pii,
)
from srag_agente.auditoria.versao import versao_codigo
from srag_agente.banco import conectar
from srag_agente.config import Config
from srag_agente.ferramentas.dicionario import BuscaDicionario
from srag_agente.ferramentas.noticias import BuscadorNoticias
from srag_agente.ferramentas.registro import (
    UF,
    Buscador,
    Dicionario,
    FonteGold,
    criar_ferramentas,
    especificacoes,
)
from srag_agente.guardrails.entrada import LIMITE_PERGUNTA, ClassificacaoEscopo, Classificador
from srag_agente.guardrails.limites import Limites
from srag_agente.guardrails.padroes import mascarar_pii
from srag_agente.metricas.repositorio import DadosIndisponiveis, RepositorioGold
from srag_agente.modelos import TextoRelatorio
from srag_agente.rag.embeddings import Embedder, criar_embedder
from srag_agente.relatorio.montar import ArquivosRelatorio, escrever_relatorio, montar_dados

log = logging.getLogger(__name__)


@dataclass
class Componentes:
    """Peças externas (LLM, notícias, embeddings). Os testes trocam por dublês.

    `provedor` e `modelo` são os do principal configurado (vão para auditoria.execucoes e para o
    relatório). Quem de fato respondeu cada chamada (principal ou reserva) fica no campo `modelo`
    de cada evento da trilha.
    """

    provedor: str
    modelo: str
    modelo_orquestrador: Runnable
    redator: Redator
    classificador: Classificador
    buscador: Buscador
    embedder: Embedder
    callbacks: list = field(default_factory=list)


@dataclass(frozen=True)
class ResultadoExecucao:
    id_execucao: UUID
    status: str
    arquivos: ArquivosRelatorio
    tokens_entrada: int
    tokens_saida: int
    custo_usd: float


def limites_da_config(cfg: Config) -> Limites:
    return Limites(
        max_chamadas_ferramenta=cfg.max_chamadas_ferramenta,
        max_tokens=cfg.max_tokens_execucao,
        max_voltas_cobertura=cfg.max_voltas_cobertura,
        max_tentativas_redacao=cfg.max_tentativas_redacao,
        max_segundos=cfg.max_segundos_execucao,
    )


# --- Observabilidade (Langfuse, opcional) ---------------------------------------------------------
def mascara_observabilidade(*, data: Any, **_: Any) -> Any:
    """Máscara do Langfuse: o que vai para fora passa pelo mesmo mascaramento da auditoria.

    Sem ela, a entrada do grafo (com a pergunta crua do usuário) iria inteira para o serviço.
    """
    return sem_pii(data)


def callbacks_observabilidade(cfg: Config) -> list:
    """Langfuse opcional: só liga se as duas chaves estiverem no .env."""
    if not (cfg.langfuse_public_key and cfg.langfuse_secret_key):
        return []
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
    except ImportError:
        log.warning(
            "Chaves do Langfuse definidas, mas o extra 'observabilidade' não está instalado "
            "(uv sync --all-extras). Seguindo sem observabilidade."
        )
        return []
    publica = cfg.langfuse_public_key.get_secret_value()
    Langfuse(
        public_key=publica,
        secret_key=cfg.langfuse_secret_key.get_secret_value(),
        host=cfg.langfuse_host,
        mask=mascara_observabilidade,
    )
    return [CallbackHandler(public_key=publica)]


def componentes_reais(cfg: Config) -> Componentes:
    modelos = criar_modelos(cfg)  # sem chave de LLM: ConfiguracaoInvalida, antes de tudo
    tavily = cfg.tavily_api_key.get_secret_value() if cfg.tavily_api_key else None
    return Componentes(
        provedor=modelos[0].provedor,
        modelo=modelos[0].modelo,
        modelo_orquestrador=modelo_com_ferramentas(modelos, especificacoes()),
        redator=criar_redator(modelo_estruturado(modelos, TextoRelatorio)),
        classificador=criar_classificador(modelo_estruturado(modelos, ClassificacaoEscopo)),
        buscador=BuscadorNoticias(httpx.Client(), tavily or None),
        embedder=criar_embedder(cfg.pasta_cache_modelos),
        callbacks=callbacks_observabilidade(cfg),
    )


# --- Execução -------------------------------------------------------------------------------------
def _referencia(repo: FonteGold, tolerar: type[BaseException]) -> tuple[date | None, int | None]:
    """Data de referência e contagem da silver vistas pela execução (None se indisponíveis)."""
    try:
        referencia = repo.referencia()
    except tolerar:
        return None, None
    return referencia.data_referencia, referencia.total_casos


def _tokens_da_trilha(auditoria: Auditoria) -> tuple[int, int]:
    """Tokens já gastos segundo os eventos gravados (o estado do grafo se perde no erro)."""
    eventos = auditoria.eventos
    return (
        sum(e["tokens_entrada"] or 0 for e in eventos),
        sum(e["tokens_saida"] or 0 for e in eventos),
    )


def _anotar_falha_da_auditoria(erro: BaseException, etapa: str, falha: Exception) -> None:
    texto = mascarar_pii(f"A auditoria também falhou ao {etapa}: {descrever_erro(falha)}")[0]
    erro.add_note(texto)
    log.error(texto)


def _encerrar_com_erro(auditoria: Auditoria, repo: FonteGold, cfg: Config,
                       erro: BaseException) -> None:  # fmt: skip
    """Registra o erro e encerra a execução como 'erro', sem nunca esconder `erro`."""
    tokens_entrada, tokens_saida = _tokens_da_trilha(auditoria)
    data_referencia, total_casos = _referencia(repo, tolerar=Exception)
    try:
        auditoria.registrar("nucleo", "decisao", erro=descrever_erro(erro))
    except Exception as falha:
        _anotar_falha_da_auditoria(erro, "registrar o erro", falha)
    try:
        auditoria.finalizar_execucao(
            status="erro",
            data_referencia=data_referencia,
            tokens_entrada=tokens_entrada,
            tokens_saida=tokens_saida,
            custo_usd=estimar_custo(tokens_entrada, tokens_saida, cfg),
            total_casos=total_casos,
        )
    except Exception as falha:
        _anotar_falha_da_auditoria(erro, "encerrar a execução", falha)


def executar_relatorio(
    cfg: Config,
    *,
    auditoria: Auditoria,
    repo: FonteGold,
    dicionario: Dicionario,
    componentes: Componentes,
    uf: str,
    pergunta_extra: str | None,
    hoje: date,
) -> ResultadoExecucao:
    """O miolo de `gerar_relatorio`, sem abrir conexões (os testes passam dublês)."""
    deps = Dependencias(
        modelo_orquestrador=componentes.modelo_orquestrador,
        redator=componentes.redator,
        classificador=componentes.classificador,
        ferramentas=criar_ferramentas(repo, componentes.buscador, dicionario, hoje=lambda: hoje),
        auditoria=auditoria,
        limites=limites_da_config(cfg),
    )
    pergunta_auditada = (
        mascarar_pii(pergunta_extra)[0][:LIMITE_PERGUNTA] if pergunta_extra else None
    )
    id_execucao = auditoria.iniciar_execucao(
        provedor=componentes.provedor,
        modelo=componentes.modelo,
        parametros={
            "uf": uf,
            "data_execucao": hoje.isoformat(),
            "pergunta_extra": pergunta_auditada,
        },
        versao_codigo=versao_codigo(),
        hash_prompts=hash_prompts(),
    )
    # Daqui até o encerramento, qualquer saída (erro, Ctrl+C) passa por _encerrar_com_erro.
    # Ordem: relatório no disco -> encerramento -> arquivo da trilha. A execução só fica "ok"
    # depois que o HTML e o Markdown existem (disco cheio ou pasta sem permissão viram "erro"),
    # e o arquivo da trilha é gravado por último porque precisa do registro de encerramento.
    try:
        estado = executar_agente(deps, uf=uf, pergunta_extra=pergunta_extra,
                                 data_execucao=hoje, callbacks=componentes.callbacks)  # fmt: skip
        status = estado["status"]
        if status not in STATUS_FINAIS:
            raise RuntimeError(f"o grafo terminou sem situação final (status={status!r})")
        data_referencia, total_casos = _referencia(repo, tolerar=DadosIndisponiveis)
        tokens_entrada, tokens_saida = estado["tokens_entrada"], estado["tokens_saida"]
        custo = estimar_custo(tokens_entrada, tokens_saida, cfg)
        dados = montar_dados(
            evidencias=estado["evidencias"], id_execucao=id_execucao, status=status, uf=uf,
            data_execucao=hoje, texto=estado["texto"], avisos=estado["avisos"],
            violacoes=estado["violacoes"], auditoria=auditoria, provedor=componentes.provedor,
            modelo=componentes.modelo, tokens_entrada=tokens_entrada, tokens_saida=tokens_saida,
            custo_usd=custo, pergunta_extra=estado["pergunta_extra"],
        )  # fmt: skip
        arquivos = escrever_relatorio(dados, cfg.pasta_relatorios)
        auditoria.finalizar_execucao(status=status, data_referencia=data_referencia,
                                     tokens_entrada=tokens_entrada, tokens_saida=tokens_saida,
                                     custo_usd=custo, total_casos=total_casos)  # fmt: skip
    except BaseException as erro:
        _encerrar_com_erro(auditoria, repo, cfg, erro)
        raise

    auditoria.gravar_jsonl(arquivos.auditoria)
    log.info("Relatório %s em %s (situação: %s)", id_execucao, arquivos.pasta, status)
    return ResultadoExecucao(id_execucao=id_execucao, status=status, arquivos=arquivos,
                             tokens_entrada=tokens_entrada, tokens_saida=tokens_saida,
                             custo_usd=custo)  # fmt: skip


def gerar_relatorio(
    cfg: Config,
    *,
    uf: str = "BR",
    pergunta_extra: str | None = None,
    hoje: date | None = None,
    componentes: Componentes | None = None,
) -> ResultadoExecucao:
    if uf not in get_args(UF):  # a CLI já confere; uma tela futura chamaria direto
        raise ValueError(f"UF inválida: {uf!r}")
    hoje = hoje or date.today()
    componentes = componentes or componentes_reais(cfg)
    with conectar(cfg, "agente", autocommit=True) as conn_agente, \
         conectar(cfg, "auditor", autocommit=True) as conn_auditor:  # fmt: skip
        return executar_relatorio(
            cfg,
            auditoria=AuditoriaPostgres(conn_auditor),
            repo=RepositorioGold(conn_agente),
            dicionario=BuscaDicionario(conn_agente, componentes.embedder),
            componentes=componentes,
            uf=uf,
            pergunta_extra=pergunta_extra,
            hoje=hoje,
        )
