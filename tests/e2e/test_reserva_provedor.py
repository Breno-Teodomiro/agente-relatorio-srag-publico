"""E2E 4 — reserva de provedor (spec §6, camada 5): o principal cai e a reserva responde.

A troca não pode ser silenciosa: cada evento de LLM grava, em `resultado.modelo`, o modelo que de
fato respondeu, no banco, no JSONL e no anexo do relatório.
"""

import pytest

from srag_agente.agente.llm import criar_classificador, criar_redator
from tests.e2e.apoio import Simulacao, chamadas_para, classificacao, eventos_no_banco, jsonl
from tests.fakes import (
    MODELO_RESERVA,
    estruturado_com_reserva,
    orquestrador_com_reserva,
    resposta_crua,
    texto_neutro,
)

pytestmark = pytest.mark.integracao


def _simulacao_com_tudo_na_reserva(pergunta: str | None) -> Simulacao:
    return Simulacao(
        pergunta=pergunta,
        orquestrador=orquestrador_com_reserva(
            resposta_crua(tool_calls=chamadas_para("BR")),
            resposta_crua(content="EVIDENCIAS_COMPLETAS"),
        ),
        redator=criar_redator(estruturado_com_reserva(texto_neutro())),
        classificador=criar_classificador(estruturado_com_reserva(classificacao())),
    )


def test_principal_fora_do_ar_registra_o_modelo_da_reserva_em_todo_evento_de_llm(
        cfg_teste, banco_carregado, conn_etl, pasta_saida):  # fmt: skip
    simulacao = _simulacao_com_tudo_na_reserva("Como está a UTI?")
    resultado = simulacao.rodar(cfg_teste, pasta_saida)

    assert resultado.status == "ok"
    eventos = eventos_no_banco(conn_etl, resultado.id_execucao)
    llm = [e for e in eventos if e["tipo"] == "llm"]
    assert {e["no"] for e in llm} == {"orquestrador", "redator"}
    assert all(e["resultado"]["modelo"] == MODELO_RESERVA for e in llm)
    (entrada,) = [e for e in eventos if e["no"] == "guardrail_entrada"]
    assert entrada["resultado"]["modelo"] == MODELO_RESERVA
    # Os tokens da reserva entram na conta da execução.
    assert resultado.tokens_entrada > 0 and resultado.custo_usd > 0


def test_modelo_da_reserva_aparece_no_jsonl_e_no_anexo_do_relatorio(cfg_teste, banco_carregado,
                                                                    pasta_saida):  # fmt: skip
    simulacao = _simulacao_com_tudo_na_reserva(None)
    resultado = simulacao.rodar(cfg_teste, pasta_saida)

    eventos = [r for r in jsonl(resultado.arquivos.auditoria) if r["tipo_registro"] == "evento"]
    llm = [e for e in eventos if e["tipo"] == "llm"]
    assert llm and all(e["resultado"]["modelo"] == MODELO_RESERVA for e in llm)
    assert f"modelo: {MODELO_RESERVA}" in simulacao.html
    assert f"modelo: {MODELO_RESERVA}" in simulacao.markdown
