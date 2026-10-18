"""Núcleo contra o Postgres de teste: agente com dublês de LLM, auditoria gravada de verdade."""

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from srag_agente.nucleo import Componentes, gerar_relatorio
from tests.fakes import CHAMADAS_COMPLETAS, HOJE, BuscadorFalso, embedder_falso, texto_neutro

pytestmark = pytest.mark.integracao

USO = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


def _chamadas():
    return [{"name": n, "args": {**a, "motivo": "evidência obrigatória do relatório"},
             "id": f"c{i}"} for i, (n, a) in enumerate(CHAMADAS_COMPLETAS)]  # fmt: skip


def _componentes(orquestrador=None):
    orquestrador = orquestrador or GenericFakeChatModel(messages=iter([
        AIMessage(content="", tool_calls=_chamadas(), usage_metadata=USO),
        AIMessage(content="EVIDENCIAS_COMPLETAS", usage_metadata=USO),
    ]))  # fmt: skip
    return Componentes(provedor="falso", modelo="falso", modelo_orquestrador=orquestrador,
                       redator=lambda contexto: (texto_neutro(), (200, 80)),
                       classificador=lambda texto: (None, (0, 0)),
                       buscador=BuscadorFalso(), embedder=embedder_falso)  # fmt: skip


def test_gera_relatorio_de_ponta_a_ponta_e_audita_no_banco(cfg_teste, banco_carregado, conn_etl,
                                                          tmp_path):  # fmt: skip
    cfg = cfg_teste.model_copy(update={"pasta_relatorios": tmp_path})
    resultado = gerar_relatorio(cfg, hoje=HOJE, componentes=_componentes())

    assert resultado.status == "ok"
    assert "Relatório de SRAG" in resultado.arquivos.html.read_text(encoding="utf-8")
    # Pelo efeito: a trilha está no banco, com o motivo de cada ferramenta.
    ferramentas = conn_etl.execute(
        "SELECT count(*) FROM auditoria.eventos WHERE id_execucao = %s AND tipo = 'ferramenta' "
        "AND motivo IS NOT NULL", (resultado.id_execucao,)).fetchone()[0]  # fmt: skip
    assert ferramentas == len(CHAMADAS_COMPLETAS)
    status, custo, total_casos, total_gold = conn_etl.execute(
        "SELECT e.status, e.custo_usd, e.total_casos, r.total_casos "
        "FROM auditoria.execucoes AS e CROSS JOIN gold.referencia AS r WHERE e.id_execucao = %s",
        (resultado.id_execucao,)).fetchone()  # fmt: skip
    assert status == "ok" and float(custo) > 0
    assert total_casos == total_gold == len(banco_carregado)


def test_erro_no_agente_fica_gravado_como_erro_com_os_tokens(cfg_teste, banco_carregado,
                                                           conn_etl, tmp_path):  # fmt: skip
    respostas = iter([AIMessage(content="", tool_calls=_chamadas(), usage_metadata=USO)])

    def orquestrador(_mensagens):
        try:
            return next(respostas)
        except StopIteration:
            raise TimeoutError("provedor de LLM não respondeu") from None

    cfg = cfg_teste.model_copy(update={"pasta_relatorios": tmp_path})
    antes = conn_etl.execute("SELECT max(inicio) FROM auditoria.execucoes").fetchone()[0]
    with pytest.raises(TimeoutError):
        gerar_relatorio(cfg, hoje=HOJE, componentes=_componentes(RunnableLambda(orquestrador)))
    conn_etl.commit()  # nova transação: enxerga o que o auditor gravou
    status, tokens_entrada, tokens_saida, fim, erro = conn_etl.execute(
        "SELECT e.status, e.tokens_entrada, e.tokens_saida, e.fim, v.erro "
        "FROM auditoria.execucoes AS e JOIN auditoria.eventos AS v USING (id_execucao) "
        "WHERE (%s::timestamptz IS NULL OR e.inicio > %s) AND v.no = 'nucleo' "
        "ORDER BY e.inicio DESC LIMIT 1", (antes, antes)).fetchone()  # fmt: skip
    assert status == "erro" and fim is not None
    assert (tokens_entrada, tokens_saida) == (100, 20)
    assert "TimeoutError" in erro
