"""E2E 3 — saída adversarial (spec §6, camada 4): o LLM redator mente e o guardrail reage.

O redator falso devolve o mesmo texto ruim nas duas tentativas. Esperado: a 2ª tentativa recebe
as violações, a execução termina como `revisao_humana` com a faixa visível, o texto reprovado é
retido (nem relatório, nem trilha) e os cartões, as tabelas e os gráficos continuam sendo os
números do SQL: o número inventado só aparece dentro da faixa, como violação listada, nunca como
dado.
"""

import json
import re
from datetime import date
from html import unescape

import pytest

from srag_agente.relatorio.montar import AVISO_RETENCAO
from tests.e2e.apoio import (
    Simulacao,
    arquivos_gerados,
    eventos_no_banco,
    texto_dos_dados,
    trilha_no_banco,
)
from tests.fakes import NOTICIAS, BuscadorFalso

pytestmark = pytest.mark.integracao

CPF_DO_LLM = "123.456.789-09"

# nome -> (seções trocadas no texto, prefixo da violação esperada)
TEXTOS_RUINS = {
    "numero_inventado": (
        {"resumo_executivo": "A mortalidade foi de 50,0% e as mortes dobraram."},
        "numero_nao_conferido: '50,0%'",
    ),
    "link_inventado": (
        {"contexto_periodo_dados": "Detalhes em https://noticias-falsas.example.com/srag [1]."},
        "link_nao_verificado",
    ),
    "noticia_inexistente": (
        {"cenario_atual": "O Ministério anunciou nova campanha nacional [7]."},
        "citacao_inexistente: [7]",
    ),
    "noticia_da_janela_errada": (
        {"cenario_atual": "O boletim do período dos dados já mostrava a alta [1]."},
        "noticia_fora_da_janela: [1]",
    ),
    "recomendacao_clinica": (
        {"limitacoes": "Recomenda-se tomar oseltamivir 75 mg a cada 12 horas."},
        "conteudo_clinico",
    ),
}


def _conferir_cartoes_do_sql(simulacao):
    """Os cartões seguem os números das evidências (SQL), com ou sem texto reprovado."""
    metricas = {m["chave"]: m for m in simulacao.contextos[0].metricas}
    secao = simulacao.markdown.split("## Métricas")[1].split("## Evolução")[0]
    mortalidade = metricas["mortalidade"]
    assert f"{mortalidade['numerador']} de {mortalidade['denominador']}" in secao


@pytest.mark.parametrize("caso", TEXTOS_RUINS)
def test_texto_reprovado_duas_vezes_vira_revisao_humana_com_faixa(
    caso, cfg_teste, banco_carregado, conn_etl, pasta_saida
):
    trocas, violacao = TEXTOS_RUINS[caso]
    simulacao = Simulacao(textos=[lambda ctx: texto_dos_dados(ctx, **trocas)])
    resultado = simulacao.rodar(cfg_teste, pasta_saida)

    assert resultado.status == "revisao_humana"
    # Uma nova tentativa, já sabendo o que corrigir; falhou de novo, para.
    assert len(simulacao.contextos) == 2
    assert not simulacao.contextos[0].violacoes_anteriores
    assert any(v.startswith(violacao) for v in simulacao.contextos[1].violacoes_anteriores)
    # Faixa visível no HTML e no Markdown, com a violação listada.
    assert '<div class="faixa perigo"><strong>Revisão humana necessária.</strong>' in simulacao.html
    assert violacao in unescape(simulacao.html)
    assert "> **Revisão humana necessária.**" in simulacao.markdown
    assert violacao.split(":")[0] in simulacao.markdown
    # A situação verdadeira chega ao banco e à trilha.
    status = conn_etl.execute("SELECT status FROM auditoria.execucoes WHERE id_execucao = %s",
                              (resultado.id_execucao,)).fetchone()[0]  # fmt: skip
    assert status == "revisao_humana"
    saidas = [e for e in eventos_no_banco(conn_etl, resultado.id_execucao)
              if e["no"] == "guardrail_saida"]  # fmt: skip
    assert len(saidas) == 2 and all(e["resultado"]["aprovado"] is False for e in saidas)
    assert saidas[-1]["resultado"]["status"] == "revisao_humana"
    assert any(v.startswith(violacao) for v in saidas[-1]["resultado"]["violacoes"])


def _texto_legivel(nome: str, conteudo: bytes) -> str:
    """O arquivo como alguém o leria: HTML sem entidades, JSONL sem escape de acento."""
    if nome.endswith(".png"):
        return conteudo.decode("latin-1")
    texto = conteudo.decode("utf-8")
    if nome.endswith(".html"):
        return unescape(texto)
    if nome.endswith(".jsonl"):
        return "\n".join(json.dumps(json.loads(linha), ensure_ascii=False)
                         for linha in texto.splitlines())  # fmt: skip
    return texto


@pytest.mark.parametrize("caso", TEXTOS_RUINS)
def test_texto_reprovado_nao_e_publicado_no_corpo_do_relatorio(
    caso, cfg_teste, banco_carregado, conn_etl, pasta_saida
):
    """Revisão humana retém o texto reprovado: nem relatório, nem trilha; o SQL continua."""
    trocas, violacao = TEXTOS_RUINS[caso]
    simulacao = Simulacao(textos=[lambda ctx: texto_dos_dados(ctx, **trocas)])
    resultado = simulacao.rodar(cfg_teste, pasta_saida)
    assert resultado.status == "revisao_humana"
    frase = next(iter(trocas.values()))
    for nome, conteudo in arquivos_gerados(resultado.arquivos.pasta).items():
        assert frase not in _texto_legivel(nome, conteudo), f"texto reprovado em {nome}"
    trilha = trilha_no_banco(conn_etl, resultado.id_execucao)
    assert frase not in trilha
    # a trilha guarda o que foi barrado (violação) e a identidade do texto retido, não ele
    saidas = [e for e in eventos_no_banco(conn_etl, resultado.id_execucao)
              if e["no"] == "guardrail_saida"]  # fmt: skip
    retido = saidas[-1]["resultado"]["texto_retido"]
    assert retido["caracteres"] > 0 and re.fullmatch(r"[0-9a-f]{64}", retido["sha256"])
    assert any(v.startswith(violacao) for v in saidas[-1]["resultado"]["violacoes"])
    # o relatório sai com o que é do SQL, a faixa e a mensagem de retenção
    _conferir_cartoes_do_sql(simulacao)
    assert "graficos/diaria_30d.png" in simulacao.markdown and "<svg" in simulacao.html
    assert '<div class="faixa perigo"><strong>Revisão humana necessária.</strong>' in simulacao.html
    assert "> **Revisão humana necessária.**" in simulacao.markdown
    assert AVISO_RETENCAO in unescape(simulacao.html) and AVISO_RETENCAO in simulacao.markdown
    assert "<h2>Resumo</h2>" not in simulacao.html and "## Resumo" not in simulacao.markdown


def test_numero_inventado_nao_vira_dado_do_relatorio(cfg_teste, banco_carregado, pasta_saida):
    simulacao = Simulacao(textos=[lambda ctx: texto_dos_dados(
        ctx, resumo_executivo="A mortalidade foi de 50,0% e as mortes dobraram.")])  # fmt: skip
    simulacao.rodar(cfg_teste, pasta_saida)
    metricas = {m["chave"]: m for m in simulacao.contextos[0].metricas}
    assert metricas["mortalidade"]["valor"] != 0.5
    cartoes = simulacao.markdown.split("## Métricas")[1].split("## Contexto")[0]
    cartoes_html = simulacao.html.split("<h2>Métricas</h2>")[1].split("<h2>Contexto")[0]
    assert "50,0%" not in cartoes and "50,0%" not in cartoes_html
    _conferir_cartoes_do_sql(simulacao)


def test_link_inventado_nao_vira_link_clicavel(cfg_teste, banco_carregado, pasta_saida):
    trocas = {"contexto_periodo_dados": "Detalhes em https://noticias-falsas.example.com/srag [1]."}
    simulacao = Simulacao(textos=[lambda ctx: texto_dos_dados(ctx, **trocas)])
    simulacao.rodar(cfg_teste, pasta_saida)
    assert 'href="https://noticias-falsas' not in simulacao.html
    assert "](https://noticias-falsas" not in simulacao.markdown
    assert "<https://noticias-falsas" not in simulacao.markdown
    # nem em texto puro, que visualizadores GFM transformam em link (autolink)
    assert "https://noticias-falsas" not in simulacao.markdown


def test_noticia_datada_fora_da_busca_e_reprovada(cfg_teste, banco_carregado, pasta_saida):
    """A fonte devolveu uma notícia de 2020 para uma busca de jun/2025: citá-la reprova."""

    class BuscadorDeOutroAno(BuscadorFalso):
        def buscar(self, termo, janela, inicio, fim):
            resultado = super().buscar(termo, janela, inicio, fim)
            resultado.noticias = [NOTICIAS[janela].model_copy(update={"data": date(2020, 1, 15)})]
            return resultado

    simulacao = Simulacao(buscador=BuscadorDeOutroAno())
    resultado = simulacao.rodar(cfg_teste, pasta_saida)
    assert resultado.status == "revisao_humana"
    assert "datada de 2020-01-15" in simulacao.html


def test_redator_que_se_corrige_na_segunda_tentativa_publica_so_o_texto_bom(
        cfg_teste, banco_carregado, pasta_saida):  # fmt: skip
    ruim = lambda ctx: texto_dos_dados(ctx, resumo_executivo="A mortalidade foi de 50,0%.")  # noqa: E731
    simulacao = Simulacao(textos=[ruim, texto_dos_dados])
    resultado = simulacao.rodar(cfg_teste, pasta_saida)
    assert resultado.status == "ok" and len(simulacao.contextos) == 2
    assert "50,0%" not in simulacao.html and "50,0%" not in simulacao.markdown
    assert "Revisão humana necessária" not in simulacao.html


def test_dado_pessoal_escrito_pelo_llm_e_mascarado_e_nao_vaza(
    cfg_teste, banco_carregado, conn_etl, pasta_saida
):
    trocas = {"limitacoes": f"A taxa de UTI não mede ocupação. Contato do caso: CPF {CPF_DO_LLM}."}
    simulacao = Simulacao(textos=[lambda ctx: texto_dos_dados(ctx, **trocas)])
    resultado = simulacao.rodar(cfg_teste, pasta_saida)
    # Dado pessoal no texto é mascarado (spec §6): não conta como violação.
    assert resultado.status == "ok"
    trilha = trilha_no_banco(conn_etl, resultado.id_execucao)
    for documento in (CPF_DO_LLM, "12345678909"):
        achados = [n for n, c in arquivos_gerados(resultado.arquivos.pasta).items()
                   if documento.encode() in c]  # fmt: skip
        assert not achados, f"{documento!r} vazou em {achados}"
        assert documento not in trilha


def test_limite_de_ferramentas_entrega_relatorio_parcial_com_faixa(
    cfg_teste, banco_carregado, conn_etl, pasta_saida
):
    """Camada 5: com o teto de chamadas, o relatório sai `parcial` e avisa o que faltou."""
    cfg = cfg_teste.model_copy(update={"max_chamadas_ferramenta": 3, "max_voltas_cobertura": 0})
    from tests.fakes import texto_neutro

    simulacao = Simulacao(textos=[texto_neutro()])
    resultado = simulacao.rodar(cfg, pasta_saida)

    assert resultado.status == "parcial"
    assert '<div class="faixa">Evidências incompletas' in simulacao.html
    assert "> Evidências incompletas" in simulacao.markdown
    status = conn_etl.execute("SELECT status FROM auditoria.execucoes WHERE id_execucao = %s",
                              (resultado.id_execucao,)).fetchone()[0]  # fmt: skip
    assert status == "parcial"
    ferramentas = [e for e in eventos_no_banco(conn_etl, resultado.id_execucao)
                   if e["tipo"] == "ferramenta"]  # fmt: skip
    assert len(ferramentas) == 3
