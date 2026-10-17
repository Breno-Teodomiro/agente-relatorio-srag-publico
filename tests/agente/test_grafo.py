import json
import re

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from srag_agente.agente.contexto import montar_contexto
from srag_agente.agente.grafo import estrutura_mermaid, executar_agente
from srag_agente.agente.llm import ErroRedacao, mensagem_do_redator
from srag_agente.agente.nos import AVISO_COBERTURA, Dependencias
from srag_agente.auditoria.registro import Auditoria
from srag_agente.guardrails.entrada import ClassificacaoEscopo
from srag_agente.guardrails.limites import Limites
from tests.fakes import (
    CHAMADAS_COMPLETAS,
    HOJE,
    BuscadorFalso,
    evidencias_completas,
    ferramentas_falsas,
    texto_neutro,
    texto_valido,
)

USO = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


def _chamadas(lista):
    return [
        {
            "name": nome,
            "args": {**args, "motivo": f"preciso de {nome} para o relatório"},
            "id": f"c{i}",
        }
        for i, (nome, args) in enumerate(lista)
    ]


def _orquestrador(*respostas):
    mensagens = [
        AIMessage(content="", tool_calls=_chamadas(r), usage_metadata=USO)
        if isinstance(r, list)
        else AIMessage(content=r, usage_metadata=USO)
        for r in respostas
    ]
    return GenericFakeChatModel(messages=iter(mensagens))


def _redator(*textos):
    fila = list(textos)

    def redigir(contexto):
        return (fila.pop(0) if len(fila) > 1 else fila[0]), (200, 80)

    return redigir


def _classificador(no_escopo=True):
    return lambda texto: (ClassificacaoEscopo(no_escopo=no_escopo, motivo="teste"), (10, 2))


def _deps(orquestrador, redator=None, limites=None, classificador=None):
    return Dependencias(
        modelo_orquestrador=orquestrador,
        redator=redator or _redator(texto_valido()),
        classificador=classificador or _classificador(),
        ferramentas=ferramentas_falsas(),
        auditoria=Auditoria(),
        limites=limites or Limites(),
    )


def _rodar(deps, pergunta=None, uf="BR"):
    deps.auditoria.iniciar_execucao(
        provedor="falso", modelo="falso", parametros={}, versao_codigo="t", hash_prompts="t"
    )
    return executar_agente(deps, uf=uf, pergunta_extra=pergunta, data_execucao=HOJE)


def test_caminho_feliz_registra_cada_ferramenta_com_motivo():
    deps = _deps(_orquestrador(CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"))
    estado = _rodar(deps)
    assert estado["status"] == "ok"
    assert len(estado["evidencias"]) == 9
    eventos = [e for e in deps.auditoria.eventos if e["tipo"] == "ferramenta"]
    assert len(eventos) == 9 and all(e["motivo"] for e in eventos)
    assert estado["tokens_entrada"] == 100 * 2 + 200


def test_cobertura_incompleta_pede_mais_ao_orquestrador():
    deps = _deps(
        _orquestrador(
            CHAMADAS_COMPLETAS[:2],
            "EVIDENCIAS_COMPLETAS",
            CHAMADAS_COMPLETAS[2:],
            "EVIDENCIAS_COMPLETAS",
        )
    )
    estado = _rodar(deps)
    assert estado["voltas_cobertura"] == 1
    assert any(
        isinstance(m, HumanMessage) and "Ainda faltam" in m.content for m in estado["messages"]
    )
    assert estado["status"] == "ok"


def test_argumento_invalido_vira_erro_para_o_orquestrador_e_a_execucao_segue():
    ruim = [("consultar_metricas", {"metrica": "uti", "uf": "ZZ"})]
    deps = _deps(_orquestrador(ruim, CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"))
    estado = _rodar(deps)
    erros = [e for e in deps.auditoria.eventos if e["tipo"] == "ferramenta" and e["erro"]]
    assert len(erros) == 1 and "Argumentos inválidos" in erros[0]["erro"]
    assert estado["status"] == "ok"


def test_argumento_invalido_nao_grava_argumentos_crus_na_auditoria():
    pii = "123.456.789-09"
    ruim = [("consultar_metricas", {"metrica": "uti", "uf": "ZZ", "pergunta_do_paciente": pii})]
    deps = _deps(_orquestrador(ruim, CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"))
    estado = _rodar(deps)
    (erro,) = [e for e in deps.auditoria.eventos if e["tipo"] == "ferramenta" and e["erro"]]
    assert erro["argumentos"] is None and erro["motivo"] is None
    trilha = json.dumps(deps.auditoria.eventos, ensure_ascii=False)
    mensagens = " ".join(str(m.content) for m in estado["messages"])
    for texto in (trilha, mensagens):
        assert pii not in texto and "pergunta_do_paciente" not in texto and "ZZ" not in texto


def test_ferramenta_desconhecida_vira_erro_sem_repetir_o_nome():
    deps = _deps(
        _orquestrador(
            [("ferramenta_do_cpf_123.456.789-09", {})], CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"
        )
    )
    _rodar(deps)
    (erro,) = [e for e in deps.auditoria.eventos if e["tipo"] == "ferramenta" and e["erro"]]
    assert erro["ferramenta"] is None and "desconhecida" in erro["erro"]
    assert "123.456" not in json.dumps(deps.auditoria.eventos)


def test_falha_da_auditoria_nao_esconde_o_erro_original_da_ferramenta():
    class AuditoriaQuebrada(Auditoria):
        def registrar(self, no, tipo, **campos):
            if no == "ferramentas" and campos.get("erro"):
                raise ConnectionError("auditor sem conexão")
            return super().registrar(no, tipo, **campos)

    ruim = [("consultar_metricas", {"metrica": "uti", "uf": "ZZ"})]
    deps = _deps(_orquestrador(ruim, "EVIDENCIAS_COMPLETAS"))
    deps.auditoria = AuditoriaQuebrada()
    with pytest.raises(ConnectionError) as info:
        _rodar(deps)
    assert info.value.__cause__ is not None
    assert "argumentos inválidos" in str(info.value.__cause__)


def test_uf_sem_casos_gera_relatorio_parcial_e_nao_quebra():
    chamadas = [
        (n, {**a, "uf": "RR"} if n != "buscar_noticias" else a) for n, a in CHAMADAS_COMPLETAS
    ]
    deps = _deps(_orquestrador(*([chamadas, "EVIDENCIAS_COMPLETAS"] * 3)))
    estado = _rodar(deps, uf="RR")
    assert estado["status"] in {"parcial", "revisao_humana"}
    assert any(a.startswith(AVISO_COBERTURA) for a in estado["avisos"])


def test_limite_de_chamadas_para_e_marca_parcial():
    deps = _deps(
        _orquestrador(CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"),
        redator=_redator(texto_neutro()),
        limites=Limites(max_chamadas_ferramenta=3),
    )
    estado = _rodar(deps)
    assert estado["chamadas_ferramenta"] == 3
    assert estado["status"] == "parcial"


def test_texto_reprovado_duas_vezes_vai_para_revisao_humana():
    errado = texto_valido(resumo_executivo="A mortalidade foi de 50,0%.")
    deps = _deps(_orquestrador(CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"), _redator(errado))
    estado = _rodar(deps)
    assert estado["tentativas_redacao"] == 2
    assert estado["status"] == "revisao_humana"
    assert any("50,0%" in v for v in estado["violacoes"])


def test_redator_corrige_na_segunda_tentativa():
    errado = texto_valido(resumo_executivo="A mortalidade foi de 50,0%.")
    deps = _deps(
        _orquestrador(CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"), _redator(errado, texto_valido())
    )
    estado = _rodar(deps)
    assert (estado["status"], estado["tentativas_redacao"]) == ("ok", 2)


def test_saida_fora_do_formato_do_redator_conta_como_tentativa_e_depois_corrige():
    anteriores = []

    def redator(contexto):
        anteriores.append(contexto.violacoes_anteriores)
        if len(anteriores) == 1:
            raise ErroRedacao("saída vazia")
        return texto_valido(), (200, 80)

    deps = _deps(_orquestrador(CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"), redator)
    estado = _rodar(deps)
    assert (estado["status"], estado["tentativas_redacao"]) == ("ok", 2)
    assert anteriores[1] and anteriores[1][0].startswith("saida_invalida")


def test_pergunta_fora_do_escopo_e_recusada_mas_o_relatorio_sai():
    deps = _deps(
        _orquestrador(CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"),
        classificador=_classificador(no_escopo=False),
    )
    estado = _rodar(deps, pergunta="Qual remédio devo tomar?")
    assert estado["pergunta_extra"] is None
    assert any("recusada" in a for a in estado["avisos"])
    assert estado["status"] == "ok"


def test_pergunta_aprovada_segue_normalizada():
    deps = _deps(_orquestrador(CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"))
    estado = _rodar(deps, pergunta="Como está a SRAG​ em SP?")  # espaço de largura zero
    assert estado["pergunta_extra"] == "Como está a SRAG em SP?"
    assert estado["status"] == "ok"


def test_sem_noticias_o_relatorio_sai_e_avisa():
    deps = _deps(_orquestrador(CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"))
    deps.ferramentas = ferramentas_falsas(buscador=BuscadorFalso(vazio=True))
    sem_citacao = texto_valido(
        contexto_periodo_dados="Não houve notícia no período.",
        cenario_atual="Não houve notícia recente.",
    )
    deps.redator = _redator(sem_citacao)
    assert _rodar(deps)["status"] == "ok"


def test_citacao_do_redator_bate_com_o_indice_que_ele_ve():
    """O [n] que o redator lê no atributo `indice` é o `id` que o guardrail de saída confere."""
    contexto = montar_contexto(evidencias_completas(), uf="BR", data_execucao=HOJE)
    mensagem = mensagem_do_redator(contexto)
    vistos = re.findall(r'<noticia_externa indice="(\d+)">\ntitulo: "([^"]*)"', mensagem)
    assert [(int(i), t) for i, t in vistos] == [(n.id, n.titulo) for n in contexto.noticias]
    assert len(vistos) == 2


def test_redator_do_grafo_le_as_noticias_pela_mensagem_do_redator_e_a_citacao_passa():
    vistas = []

    def redator(contexto):
        vistas.append(mensagem_do_redator(contexto))
        return texto_valido(), (200, 80)  # cita [1] e [2]

    deps = _deps(_orquestrador(CHAMADAS_COMPLETAS, "EVIDENCIAS_COMPLETAS"), redator)
    estado = _rodar(deps)
    assert estado["status"] == "ok"
    assert 'indice="1"' in vistas[0] and 'indice="2"' in vistas[0]


def test_estrutura_mermaid_traz_os_nos_do_grafo():
    mermaid = estrutura_mermaid()
    for no in ("guardrail_entrada", "orquestrador", "ferramentas", "verificar_cobertura",
               "redator", "guardrail_saida"):  # fmt: skip
        assert no in mermaid
