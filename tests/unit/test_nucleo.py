"""Núcleo sem banco: encerramento da execução em sucesso e em erro, tokens, limites, PII.

A versão com Postgres de verdade está em tests/integracao/test_nucleo.py.
"""

import json
import logging
import sys
import types
from dataclasses import replace

import psycopg
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, SecretStr

from srag_agente import nucleo
from srag_agente.auditoria.registro import Auditoria, sem_pii
from srag_agente.config import Config
from srag_agente.guardrails.entrada import LIMITE_PERGUNTA
from srag_agente.guardrails.limites import Limites
from srag_agente.guardrails.padroes import MASCARA
from srag_agente.nucleo import Componentes, executar_relatorio, limites_da_config
from tests.fakes import (
    CHAMADAS_COMPLETAS,
    HOJE,
    REF,
    BuscadorFalso,
    BuscadorSemData,
    DicionarioFalso,
    RepositorioFalso,
    embedder_falso,
    texto_neutro,
    texto_valido,
)

USO = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
CPF = "123.456.789-09"  # fictício


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(_env_file=None, pg_senha_admin="a", pg_senha_etl="e", pg_senha_agente="g",
                  pg_senha_auditor="u", pasta_relatorios=tmp_path)  # fmt: skip


def _chamadas():
    return [{"name": n, "args": {**a, "motivo": "evidência obrigatória do relatório"},
             "id": f"c{i}"} for i, (n, a) in enumerate(CHAMADAS_COMPLETAS)]  # fmt: skip


def _orquestrador_ok():
    return GenericFakeChatModel(messages=iter([
        AIMessage(content="", tool_calls=_chamadas(), usage_metadata=USO),
        AIMessage(content="EVIDENCIAS_COMPLETAS", usage_metadata=USO),
    ]))  # fmt: skip


def _orquestrador_que_quebra(erro: BaseException):
    """1ª volta pede as ferramentas (gasta tokens); a 2ª levanta `erro`."""
    respostas = iter([AIMessage(content="", tool_calls=_chamadas(), usage_metadata=USO)])

    def invocar(_mensagens):
        try:
            return next(respostas)
        except StopIteration:
            raise erro from None

    return RunnableLambda(invocar)


def _componentes(orquestrador=None) -> Componentes:
    return Componentes(provedor="falso", modelo="falso",
                       modelo_orquestrador=orquestrador or _orquestrador_ok(),
                       redator=lambda contexto: (texto_neutro(), (200, 80), None),
                       classificador=lambda texto: (None, (0, 0), None),
                       buscador=BuscadorFalso(), embedder=embedder_falso)  # fmt: skip


def _rodar(cfg, auditoria=None, orquestrador=None, **kwargs):
    auditoria = auditoria if auditoria is not None else Auditoria()
    resultado = executar_relatorio(
        cfg, auditoria=auditoria, repo=RepositorioFalso(), dicionario=DicionarioFalso(),
        componentes=_componentes(orquestrador), uf=kwargs.get("uf", "BR"),
        pergunta_extra=kwargs.get("pergunta_extra"), hoje=HOJE,
    )  # fmt: skip
    return resultado, auditoria


# --- sucesso ----------------------------------------------------------------------------------
def test_sucesso_encerra_com_total_de_casos_e_consumo(cfg):
    resultado, auditoria = _rodar(cfg)
    e = auditoria.execucao
    assert resultado.status == e["status"] == "ok"
    # a contagem da silver que a execução viu (gold.referencia.total_casos)
    assert e["total_casos"] == RepositorioFalso().referencia().total_casos
    assert e["data_referencia"] == REF.isoformat()
    # 2 voltas do orquestrador (100/20 cada) + redator (200/80)
    assert (e["tokens_entrada"], e["tokens_saida"]) == (400, 120)
    assert (resultado.tokens_entrada, resultado.tokens_saida) == (400, 120)
    assert resultado.custo_usd > 0
    assert e["custo_usd"] == pytest.approx(round(resultado.custo_usd, 6))
    assert resultado.arquivos.html.is_file() and resultado.arquivos.auditoria.is_file()


def test_pergunta_com_dado_pessoal_entra_mascarada_nos_parametros(cfg):
    _, auditoria = _rodar(cfg, pergunta_extra=f"meu cpf é {CPF}, como está a UTI?")
    pergunta = auditoria.execucao["parametros"]["pergunta_extra"]
    assert MASCARA in pergunta and "456" not in pergunta


def test_pergunta_longa_entra_cortada_nos_parametros(cfg):
    _, auditoria = _rodar(cfg, pergunta_extra="Como está a UTI? " + "x" * (LIMITE_PERGUNTA * 3))
    pergunta = auditoria.execucao["parametros"]["pergunta_extra"]
    assert pergunta.startswith("Como está a UTI?") and len(pergunta) == LIMITE_PERGUNTA


def test_limites_da_config_chegam_as_dependencias(cfg, monkeypatch):
    novos = {"max_segundos_execucao": 7.5, "max_chamadas_ferramenta": 9,
             "max_voltas_cobertura": 1, "max_tentativas_redacao": 3}  # fmt: skip
    cfg = cfg.model_copy(update=novos)
    assert limites_da_config(cfg) == Limites(
        max_chamadas_ferramenta=9, max_tokens=cfg.max_tokens_execucao, max_voltas_cobertura=1,
        max_tentativas_redacao=3, max_segundos=7.5,
    )  # fmt: skip
    vistas = []
    original = nucleo.executar_agente

    def espiao(deps, **kwargs):
        vistas.append(deps.limites)
        return original(deps, **kwargs)

    monkeypatch.setattr(nucleo, "executar_agente", espiao)
    _rodar(cfg)
    assert vistas == [limites_da_config(cfg)]


# --- erro -------------------------------------------------------------------------------------
def test_erro_no_agente_encerra_como_erro_e_registra_os_tokens_gastos(cfg):
    auditoria = Auditoria()
    with pytest.raises(RuntimeError, match="LLM caiu"):
        _rodar(cfg, auditoria, _orquestrador_que_quebra(RuntimeError("LLM caiu")))
    e = auditoria.execucao
    assert e["status"] == "erro"
    # a 1ª volta do orquestrador gastou 100/20 antes de quebrar: o custo não some
    assert (e["tokens_entrada"], e["tokens_saida"]) == (100, 20) and e["custo_usd"] > 0
    assert e["total_casos"] == RepositorioFalso().referencia().total_casos
    ultimo = auditoria.eventos[-1]
    assert (ultimo["no"], ultimo["tipo"]) == ("nucleo", "decisao")
    assert "RuntimeError: LLM caiu" in ultimo["erro"]


def test_ctrl_c_tambem_encerra_a_execucao(cfg):
    auditoria = Auditoria()
    with pytest.raises(KeyboardInterrupt):
        _rodar(cfg, auditoria, _orquestrador_que_quebra(KeyboardInterrupt()))
    assert auditoria.execucao["status"] == "erro"


def test_erro_de_validacao_nao_grava_o_valor_recebido(cfg):
    class Modelo(BaseModel):
        quantidade: int

    try:
        Modelo(quantidade=f"texto cru {CPF} que não pode ir")
    except Exception as exc:
        erro = exc
    auditoria = Auditoria()
    with pytest.raises(type(erro)):
        _rodar(cfg, auditoria, _orquestrador_que_quebra(erro))
    gravado = auditoria.eventos[-1]["erro"]
    assert "ValidationError" in gravado and "quantidade" in gravado
    assert "texto cru" not in gravado and "456" not in gravado


def test_dado_pessoal_no_texto_do_erro_e_mascarado(cfg):
    auditoria = Auditoria()
    with pytest.raises(RuntimeError):
        _rodar(cfg, auditoria, _orquestrador_que_quebra(RuntimeError(f"falhou para {CPF}")))
    assert "456" not in auditoria.eventos[-1]["erro"]


class AuditoriaQueCai(Auditoria):
    """O auditor perde a conexão quando o núcleo vai registrar o erro."""

    def _persistir_evento(self, evento):
        if evento["no"] == "nucleo":
            raise psycopg.OperationalError("auditor: server closed the connection")
        return {}

    def _persistir_fim(self, encerramento):
        raise psycopg.OperationalError("auditor: server closed the connection")


def test_falha_do_auditor_nao_esconde_o_erro_original(cfg, caplog):
    auditoria = AuditoriaQueCai()
    with caplog.at_level(logging.ERROR, logger="srag_agente.nucleo"), \
         pytest.raises(RuntimeError, match="LLM caiu") as capturado:  # fmt: skip
        _rodar(cfg, auditoria, _orquestrador_que_quebra(RuntimeError("LLM caiu")))
    # o erro que sobe é o original; as duas falhas da auditoria vão junto, como nota e no log
    notas = "\n".join(getattr(capturado.value, "__notes__", []))
    assert "registrar o erro" in notas and "encerrar a execução" in notas
    assert "OperationalError" in notas
    assert "OperationalError" in caplog.text
    assert not auditoria.encerrada  # não houve como encerrar: não se finge que encerrou


def test_falha_do_auditor_com_dado_pessoal_e_anotada_mascarada(cfg, caplog):
    class CaiComCpf(Auditoria):
        def _persistir_evento(self, evento):
            if evento["no"] == "nucleo":
                raise psycopg.OperationalError(f"linha rejeitada: {CPF}")
            return {}

    with caplog.at_level(logging.ERROR, logger="srag_agente.nucleo"), \
         pytest.raises(RuntimeError, match="LLM caiu") as capturado:  # fmt: skip
        _rodar(cfg, CaiComCpf(), _orquestrador_que_quebra(RuntimeError("LLM caiu")))
    notas = "\n".join(capturado.value.__notes__)
    assert "registrar o erro" in notas and MASCARA in notas
    assert "456" not in notas and "456" not in caplog.text


def test_auditor_que_cai_no_meio_da_execucao_continua_visivel(cfg):
    class CaiNoOrquestrador(Auditoria):
        def _persistir_evento(self, evento):
            raise psycopg.OperationalError("auditor caiu")

        def _persistir_fim(self, encerramento):
            raise psycopg.OperationalError("auditor caiu de novo")

    with pytest.raises(psycopg.OperationalError, match="auditor caiu") as capturado:
        _rodar(cfg, CaiNoOrquestrador())
    assert any("auditor caiu de novo" in n for n in capturado.value.__notes__)


def test_situacao_desconhecida_do_grafo_vira_erro(cfg, monkeypatch):
    original = nucleo.executar_agente

    def sem_situacao_final(deps, **kwargs):
        return {**original(deps, **kwargs), "status": "em_andamento"}

    monkeypatch.setattr(nucleo, "executar_agente", sem_situacao_final)
    auditoria = Auditoria()
    with pytest.raises(RuntimeError, match="em_andamento"):
        _rodar(cfg, auditoria)
    assert auditoria.execucao["status"] == "erro"


# --- I1: a trilha só diz "ok" depois que o relatório existe -----------------------------------
@pytest.mark.parametrize(
    "falha",
    [OSError("disco cheio"), PermissionError("relatorios/ sem permissão"), KeyboardInterrupt()],
    ids=["disco_cheio", "sem_permissao", "ctrl_c"],
)
def test_falha_ao_gravar_o_relatorio_encerra_como_erro(cfg, monkeypatch, falha):
    def quebra(*_args, **_kwargs):
        raise falha

    monkeypatch.setattr(nucleo, "escrever_relatorio", quebra)
    auditoria = Auditoria()
    with pytest.raises(type(falha)):
        _rodar(cfg, auditoria)
    e = auditoria.execucao
    assert e["status"] == "erro"  # nunca "ok" sem relatório gravado
    # o consumo do agente (400/120) entra no encerramento de erro: o gasto não some
    assert (e["tokens_entrada"], e["tokens_saida"]) == (400, 120) and e["custo_usd"] > 0
    ultimo = auditoria.eventos[-1]
    assert (ultimo["no"], ultimo["tipo"]) == ("nucleo", "decisao")
    assert type(falha).__name__ in ultimo["erro"]


def test_erro_no_calculo_do_custo_nao_deixa_a_execucao_em_andamento(cfg, monkeypatch):
    original, chamadas = nucleo.estimar_custo, []

    def quebra_na_primeira(*args):
        chamadas.append(args)
        if len(chamadas) == 1:  # o cálculo do sucesso; o do encerramento de erro funciona
            raise ValueError("preço inválido")
        return original(*args)

    monkeypatch.setattr(nucleo, "estimar_custo", quebra_na_primeira)
    auditoria = Auditoria()
    with pytest.raises(ValueError, match="preço inválido"):
        _rodar(cfg, auditoria)
    assert auditoria.execucao["status"] == "erro"


def test_trilha_gravada_no_arquivo_traz_o_encerramento(cfg):
    resultado, _ = _rodar(cfg)
    primeira = json.loads(resultado.arquivos.auditoria.read_text(encoding="utf-8").splitlines()[0])
    assert primeira["tipo_registro"] == "execucao"
    assert primeira["status"] == "ok" and "fim" in primeira


def test_falha_ao_encerrar_depois_do_relatorio_vira_erro(cfg):
    """O relatório foi escrito, mas o auditor caiu ao gravar o "ok": tenta encerrar como erro."""

    class CaiNoFimOk(Auditoria):
        def _persistir_fim(self, encerramento):
            if encerramento["status"] == "ok":
                raise psycopg.OperationalError("auditor caiu no encerramento")
            return {}

    auditoria = CaiNoFimOk()
    with pytest.raises(psycopg.OperationalError, match="auditor caiu no encerramento"):
        _rodar(cfg, auditoria)
    assert auditoria.execucao["status"] == "erro"


def test_falha_ao_abrir_a_execucao_nao_roda_o_agente(cfg):
    class NaoAbre(Auditoria):
        def _persistir_inicio(self):
            raise psycopg.OperationalError("auditor fora do ar")

    chamado = []
    orquestrador = RunnableLambda(lambda m: chamado.append(m))
    with pytest.raises(psycopg.OperationalError):
        _rodar(cfg, NaoAbre(), orquestrador)
    assert chamado == []


def test_uf_invalida_e_recusada_antes_de_conectar(cfg):
    cfg = cfg.model_copy(update={"pg_porta": 1})  # se tentasse conectar, o erro seria outro
    with pytest.raises(ValueError, match="UF inválida"):
        nucleo.gerar_relatorio(cfg, uf="ZZ", hoje=HOJE, componentes=_componentes())


# --- observabilidade --------------------------------------------------------------------------
def test_sem_chaves_do_langfuse_nao_ha_callback(cfg):
    assert nucleo.callbacks_observabilidade(cfg) == []


def test_chaves_sem_o_pacote_avisam_e_seguem(tmp_path, monkeypatch, caplog):
    # chaves pelo Config de verdade (viram SecretStr), como chegam do .env
    chaves = {"langfuse_public_key": "pk-lf-publica", "langfuse_secret_key": "sk-lf-secreta"}
    cfg = Config(_env_file=None, pg_senha_admin="a", pg_senha_etl="e", pg_senha_agente="g",
                 pg_senha_auditor="u", pasta_relatorios=tmp_path, **chaves)  # fmt: skip
    assert isinstance(cfg.langfuse_secret_key, SecretStr)
    monkeypatch.setitem(sys.modules, "langfuse", None)  # import falha como se não instalado
    monkeypatch.setitem(sys.modules, "langfuse.langchain", None)
    with caplog.at_level(logging.WARNING):
        assert nucleo.callbacks_observabilidade(cfg) == []
    assert "observabilidade" in caplog.text
    assert "sk-lf-secreta" not in caplog.text and "pk-lf-publica" not in caplog.text


def test_langfuse_liga_com_mascara_de_dado_pessoal(cfg, monkeypatch):
    criados = {}

    class LangfuseFalso:
        def __init__(self, **kwargs):
            criados.update(kwargs)

    class Handler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    pacote = types.ModuleType("langfuse")
    pacote.Langfuse = LangfuseFalso
    integracao = types.ModuleType("langfuse.langchain")
    integracao.CallbackHandler = Handler
    monkeypatch.setitem(sys.modules, "langfuse", pacote)
    monkeypatch.setitem(sys.modules, "langfuse.langchain", integracao)
    cfg = cfg.model_copy(update={"langfuse_public_key": SecretStr("pk-1"),
                                 "langfuse_secret_key": SecretStr("sk-1")})  # fmt: skip

    (handler,) = nucleo.callbacks_observabilidade(cfg)
    assert isinstance(handler, Handler) and handler.kwargs == {"public_key": "pk-1"}
    assert criados["public_key"] == "pk-1" and criados["secret_key"] == "sk-1"
    assert criados["host"] == cfg.langfuse_host  # `host` existe do langfuse 3 ao 5
    # o que vai ao Langfuse (entrada do grafo, mensagens) passa pela mesma máscara da auditoria
    mascara = criados["mask"]
    dados = mascara(data={"pergunta_extra": f"cpf {CPF}", "mensagens": [f"tel {CPF}", 3]})
    assert "456" not in str(dados) and dados["mensagens"][1] == 3


# --- revisão final da branch ------------------------------------------------------------------
def test_aviso_de_noticia_sem_data_chega_ao_relatorio_gravado(cfg):
    """I1 pelo efeito: o aviso do guardrail de saída aparece no Markdown e na trilha JSONL."""
    componentes = replace(_componentes(), buscador=BuscadorSemData(),
                          redator=lambda contexto: (texto_valido(), (200, 80), None))  # fmt: skip
    resultado = executar_relatorio(
        cfg, auditoria=Auditoria(), repo=RepositorioFalso(), dicionario=DicionarioFalso(),
        componentes=componentes, uf="BR", pergunta_extra=None, hoje=HOJE,
    )  # fmt: skip
    assert resultado.status == "ok"
    assert "noticia_sem_data" in resultado.arquivos.markdown.read_text(encoding="utf-8")
    assert "noticia_sem_data" in resultado.arquivos.auditoria.read_text(encoding="utf-8")


def test_mascara_do_langfuse_usa_o_mesmo_mascaramento_da_auditoria():
    entrada = {f"cpf {CPF}": (f"pergunta {CPF}", 12345678909, 0.5, None), "uf": "BR"}
    assert nucleo.mascara_observabilidade(data=entrada) == sem_pii(entrada)
    assert nucleo.mascara_observabilidade(data=entrada) == {
        f"cpf {MASCARA}": [f"pergunta {MASCARA}", MASCARA, 0.5, None], "uf": "BR",
    }  # fmt: skip
