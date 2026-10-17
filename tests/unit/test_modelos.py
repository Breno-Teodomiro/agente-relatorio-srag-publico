"""Modelos compartilhados: texto do redator e numeração das notícias citáveis."""

from datetime import date

import pytest
from pydantic import ValidationError

from srag_agente.ferramentas.registro import Evidencia
from srag_agente.modelos import ComentarioMetrica, TextoRelatorio, numerar_noticias
from tests.fakes import evidencias_completas, texto_neutro, texto_valido


def _busca(janela, noticias, inicio="2025-05-26", fim="2025-06-25"):
    return Evidencia(
        ferramenta="buscar_noticias",
        argumentos={"janela": janela},
        dados={"janela": janela, "inicio": inicio, "fim": fim, "noticias": noticias},
    )


def _noticia(url, janela="periodo_dos_dados", data="2025-06-20", **extra):
    return {"titulo": "t", "fonte": "f", "url": url, "data": data, "trecho": "x",
            "confiavel": True, "janela": janela, **extra}  # fmt: skip


def test_numera_as_noticias_das_duas_buscas_em_ordem():
    noticias = numerar_noticias(evidencias_completas())
    assert [(n.id, n.janela) for n in noticias] == [(1, "periodo_dos_dados"), (2, "cenario_atual")]
    assert noticias[0].inicio_janela == date(2025, 5, 26)
    assert noticias[0].fim_janela == date(2025, 6, 25)


def test_url_repetida_nao_ganha_numero_novo():
    evidencias = [
        _busca("periodo_dos_dados", [_noticia("https://a.br/1"), _noticia("https://a.br/1")]),
        _busca("cenario_atual", [_noticia("https://a.br/1", "cenario_atual")]),
    ]
    assert [n.url for n in numerar_noticias(evidencias)] == ["https://a.br/1"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "", "ftp://a.br/x"])
def test_url_insegura_nao_vira_citacao(url):
    assert numerar_noticias([_busca("periodo_dos_dados", [_noticia(url)])]) == []


def test_a_janela_que_vale_e_a_da_busca_que_rodou():
    # item que se declara de outra janela não muda de seção por conta própria
    evidencias = [_busca("periodo_dos_dados", [_noticia("https://a.br/1", "cenario_atual")])]
    assert numerar_noticias(evidencias)[0].janela == "periodo_dos_dados"


def test_ignora_evidencias_que_nao_sao_noticias():
    assert numerar_noticias([e for e in evidencias_completas()
                             if e.ferramenta != "buscar_noticias"]) == []  # fmt: skip


def test_secoes_e_com_secoes_fazem_ida_e_volta():
    texto = texto_valido(resposta_pergunta_extra="Resposta.")
    secoes = texto.secoes()
    assert set(secoes) == {
        "resumo_executivo", "contexto_periodo_dados", "cenario_atual", "limitacoes",
        "resposta_pergunta_extra", "comentario_aumento_casos", "comentario_mortalidade",
        "comentario_uti", "comentario_vacinacao",
    }  # fmt: skip
    trocado = texto.com_secoes({nome: f"[{nome}]" for nome in secoes})
    assert trocado.secoes() == {nome: f"[{nome}]" for nome in secoes}
    assert texto.secoes() == secoes  # o original não muda


def test_sem_pergunta_extra_a_secao_nao_aparece():
    assert "resposta_pergunta_extra" not in texto_neutro().secoes()


def test_comentario_repetido_para_a_mesma_metrica_e_recusado():
    # com dois comentários de UTI, só um seria conferido e o outro sairia sem guardrail
    comentarios = [
        *texto_neutro().comentarios_metricas,
        ComentarioMetrica(metrica="uti", comentario="9%"),
    ]
    with pytest.raises(ValidationError, match="repetid"):
        TextoRelatorio(**{**texto_neutro().model_dump(), "comentarios_metricas": comentarios})


def test_com_secoes_aceita_texto_que_cresceu_com_a_mascara():
    texto = texto_valido(
        comentarios_metricas=[ComentarioMetrica(metrica="uti", comentario="a" * 690 + " a@b.co")]
    )
    secoes = texto.secoes() | {"comentario_uti": "a" * 690 + " [dado removido]"}
    assert texto.com_secoes(secoes).comentarios_metricas[0].comentario.endswith("removido]")
