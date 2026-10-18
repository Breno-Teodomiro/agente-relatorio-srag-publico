"""A tela (app.py) sob `AppTest`: "ocupado" -> "resultado" com uma execução completa, e o modo
demonstração (banner, teto diário, recusas na partida, erros sem infraestrutura).

O `run_every` do fragmento não é simulado: o segundo `run()` faz o papel do refresh. Nenhum teste
fala com banco: as leituras do teto são dublês e `conectar` falha se for chamado.
"""

import logging
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

# `streamlit` é extra opcional (`app`): sem ele, este arquivo é pulado em vez de quebrar a coleta.
pytest.importorskip("streamlit")

import psycopg
import streamlit as st
from streamlit import config as st_config
from streamlit.testing.v1 import AppTest

from srag_agente import nucleo, tela
from srag_agente.config import Config
from srag_agente.demo import teto
from srag_agente.ferramentas.dicionario import TrechoDicionario
from srag_agente.rag import consulta, embeddings

RAIZ = Path(__file__).resolve().parents[2]
APP = str(RAIZ / "src" / "srag_agente" / "app.py")
ENTRADA_DEMO = str(RAIZ / "demo" / "streamlit_app.py")

HOST = "ep-canario-123.sa-east-1.aws.neon.tech"
SENHA = "canario-senha-9f8e7d"  # fictícia: só marca para procurar na tela e no log
SEGREDOS_DEMO = {
    "DEMO_ONLINE": "1",
    "BUSCA_DICIONARIO": "texto",
    "PG_HOST": HOST,
    "PG_PORTA": "5432",
    "PG_BANCO": "neondb",
    "PG_SSLMODE": "require",
    "PG_SENHA_AGENTE": SENHA,
    "PG_SENHA_AUDITOR": SENHA + "-auditor",
    "OPENROUTER_API_KEY": "chave-canario-or",
    "MAX_RELATORIOS_SESSAO": "2",
}
USO_LIVRE = teto.UsoDiario(3, 0, 0.1, 4, 10, 40, 1.0, datetime(2026, 10, 19, 3, tzinfo=UTC))
USO_NO_TETO = teto.UsoDiario(10, 0, 0.5, 20, 10, 40, 1.0, datetime(2026, 10, 19, 3, tzinfo=UTC))
PACOTE = teto.PacoteDemo("0123456789abcdef" * 4, "ded6a6e", date(2025, 6, 25))


IP_PUBLICO_FICTICIO = "93.184.216.34"


@pytest.fixture(autouse=True)
def _dns_ficticio(monkeypatch):
    """`host_local` resolve nomes; aqui todo nome resolve para um IP público fixo (sem rede)."""
    import srag_agente.demo as demo_pkg

    monkeypatch.setattr(demo_pkg.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", (IP_PUBLICO_FICTICIO, 0))])  # fmt: skip


@pytest.fixture(autouse=True)
def _ambiente(request, monkeypatch):
    st.cache_resource.clear()  # cache_resource é global; session_id é fixo no AppTest
    st.cache_data.clear()
    monkeypatch.delenv("DEMO_ONLINE", raising=False)
    monkeypatch.delenv("demo_online", raising=False)
    if "demo" not in request.fixturenames:
        monkeypatch.setattr(Config, "__init__", lambda self, **_: None)
        monkeypatch.setattr(Config, "max_relatorios_sessao", 5, raising=False)
        monkeypatch.setattr(Config, "demo_online", False, raising=False)
        monkeypatch.setattr(Config, "busca_dicionario", "vetor", raising=False)
    monkeypatch.setattr(nucleo, "componentes_reais", lambda cfg: None)
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


@contextmanager
def _opcao(nome: str, valor):
    antes = st_config.get_option(nome)
    st_config.set_option(nome, valor)
    try:
        yield
    finally:
        st_config.set_option(nome, antes)


def _sem_banco(*_a, **_k):
    raise AssertionError("o teste tentou abrir conexão de verdade")


class _Relogio:
    agora = 1000.0

    def __call__(self):
        return self.agora


@pytest.fixture
def relogio(monkeypatch):
    r = _Relogio()
    monkeypatch.setattr(tela, "relogio", r)
    return r


@pytest.fixture
def demo(monkeypatch, tmp_path, relogio):
    """Config real só dos segredos (sem .env nem variável de ambiente) e banco em dublê."""
    monkeypatch.setitem(Config.model_config, "env_file", None)
    for campo in Config.model_fields:
        monkeypatch.delenv(campo.upper(), raising=False)
        monkeypatch.delenv(campo, raising=False)
    monkeypatch.setattr(teto, "conectar", _sem_banco)
    monkeypatch.setattr(consulta, "conectar", _sem_banco)
    monkeypatch.setattr(teto, "prontidao_banco_demo", lambda cfg: [])
    monkeypatch.setattr(teto, "uso_de_hoje", lambda cfg: USO_LIVRE)
    monkeypatch.setattr(teto, "pacote_carregado", lambda cfg: PACOTE)

    def sem_modelo(*_a, **_k):
        raise AssertionError("o modo texto não pode carregar o modelo de embeddings")

    monkeypatch.setattr(embeddings, "criar_embedder", sem_modelo)
    monkeypatch.setattr(consulta, "criar_embedder", sem_modelo)
    return {**SEGREDOS_DEMO, "PASTA_RELATORIOS": str(tmp_path / "relatorios")}


def _app(segredos: dict | None = None, arquivo: str = APP) -> AppTest:
    at = AppTest.from_file(arquivo, default_timeout=30)
    # Sempre um dicionário não vazio: o AppTest não lê um secrets.toml que exista na máquina.
    at.secrets = segredos if segredos else {"_teste": "1"}
    return at


def _textos(elementos) -> list[str]:
    return [e.value for e in elementos]


def _clicar_gerar(at: AppTest) -> AppTest:
    return at.button[0].click().run()


def test_ocupado_avisa_e_mantem_o_resultado_da_propria_sessao_ate_o_novo_chegar(monkeypatch):
    anterior = tela.RelatorioTela("concluído", "sucesso", "<p>a</p>", "{}\n", 0.01)
    novo = tela.RelatorioTela("parcial", "aviso", "<p>b</p>", "{}\n", 0.02)
    vistas = []

    def gerar(*a, **k):
        # a geração está em curso (trava tomada): outra execução da tela vê "ocupado"
        vistas.append(_clicar_gerar(_app().run()))
        return novo

    monkeypatch.setattr(tela, "gerar_para_tela", lambda *a, **k: anterior)
    primeira = _app().run()
    _clicar_gerar(primeira)  # grava o resultado "anterior" no armazém da sessão
    monkeypatch.setattr(tela, "gerar_para_tela", gerar)
    _clicar_gerar(_app().run())

    (ocupada,) = vistas
    assert any("outro relatório está em geração" in t.lower() for t in _textos(ocupada.info))
    assert any("concluído" in t for t in _textos(ocupada.success))  # o dela segue na tela

    depois = _app().run()  # o fragmento reabre sem a trava: aparece o resultado novo
    assert not depois.info
    assert any("parcial" in t for t in _textos(depois.warning))


# --- modo local de sempre ---


def test_local_fora_de_127_recusa_antes_de_tudo():
    with _opcao("server.address", "0.0.0.0"):
        at = _app().run()
    assert any("só roda em 127.0.0.1" in t for t in _textos(at.error))
    assert not at.tabs


def test_local_mostra_a_pergunta_complementar_e_sem_banner():
    at = _app().run()
    assert len(at.text_input) == 2  # pergunta complementar + dicionário
    assert not at.info


def test_demo_pedido_em_fonte_que_o_config_nao_usa_nao_pula_a_regra_do_endereco(monkeypatch):
    # variável de ambiente pede demo, mas o Config (dublê local) diz que não: a regra 127.0.0.1 vale
    monkeypatch.setenv("DEMO_ONLINE", "1")
    with _opcao("server.address", "0.0.0.0"):
        at = _app().run()
    assert any("só roda em 127.0.0.1" in t for t in _textos(at.error))
    assert not at.tabs


# --- modo demonstração ---


def _todo_texto(at: AppTest) -> str:
    partes = []
    for tipo in ("error", "warning", "info", "success", "markdown", "caption", "text", "title"):
        partes += [str(e.value) for e in getattr(at, tipo)]
    return "\n".join(partes)


def test_demo_abre_com_banner_rodape_e_sem_pergunta_complementar(demo):
    at = _app(demo).run()
    assert not at.error and not at.exception
    banner = _textos(at.info)[0]
    assert "Demonstração pública" in banner and "hoje: 3 de 10" in banner
    assert "25/06/2025" in banner
    assert any("0123456789ab" in t and "texto completo" in t for t in _textos(at.caption))
    assert len(at.text_input) == 1  # só o do dicionário
    assert not at.button[0].disabled


def test_demo_nao_depende_do_endereco_local(demo):
    with _opcao("server.address", "0.0.0.0"):
        at = _app(demo).run()
    assert not at.error and at.tabs


def test_demo_com_host_local_recusa_sem_listar_o_motivo(demo, caplog):
    caplog.set_level(logging.INFO)
    at = _app({**demo, "PG_HOST": "localhost"}).run()
    assert _textos(at.error) == [tela.MENSAGEM_DEMO_INDISPONIVEL]
    assert not at.tabs
    assert "PG_HOST" in caplog.text  # o motivo vai só para o log do servidor


def test_demo_registra_as_opcoes_observadas_uma_vez_sem_segredo(demo, caplog):
    caplog.set_level(logging.INFO)
    with _opcao("client.showErrorDetails", False):
        at = _app(demo).run()
        at.run()
    marca = "opções do Streamlit observadas"
    linhas = [r.getMessage() for r in caplog.records if marca in r.getMessage()]
    assert len(linhas) == 1  # uma vez por processo
    assert "server.enableXsrfProtection" in linhas[0]
    # o valor que a plataforma aplicou, registrado antes da troca para "none"
    assert "'client.showErrorDetails': 'False'" in linhas[0]
    for proibido in (HOST, SENHA):
        assert proibido not in caplog.text


def test_demo_recusa_por_opcao_registra_o_valor_lido(demo, caplog):
    caplog.set_level(logging.INFO)
    with _opcao("client.toolbarMode", "minimal"):
        at = _app(demo).run()
    assert _textos(at.error) == [tela.MENSAGEM_DEMO_INDISPONIVEL]
    assert "client.toolbarMode lido='minimal' (exigido 'viewer')" in caplog.text
    assert "minimal" not in _todo_texto(at)


def test_demo_com_senha_de_dono_recusa(demo):
    at = _app({**demo, "PG_SENHA_ADMIN": "dono"}).run()
    assert _textos(at.error) == [tela.MENSAGEM_DEMO_INDISPONIVEL]
    assert not at.tabs


def test_demo_com_opcao_relaxada_recusa(demo, caplog):
    caplog.set_level(logging.INFO)
    with _opcao("client.toolbarMode", "developer"):
        at = _app(demo).run()
    assert _textos(at.error) == [tela.MENSAGEM_DEMO_INDISPONIVEL]
    assert "client.toolbarMode" in caplog.text


def _forcadas_pela_plataforma():
    from contextlib import ExitStack

    pilha = ExitStack()
    for nome, valor in (("client.showErrorDetails", False), ("server.runOnSave", True),
                        ("browser.gatherUsageStats", True), ("runner.fastReruns", True),
                        ("server.enableXsrfProtection", False),
                        ("server.enableCORS", False)):  # fmt: skip
        pilha.enter_context(_opcao(nome, valor))
    return pilha


def test_demo_abre_com_os_valores_que_a_plataforma_forca(demo):
    with _forcadas_pela_plataforma():
        at = _app(demo).run()
        detalhe = st_config.get_option("client.showErrorDetails")
    assert not at.error and at.tabs
    assert detalhe == "none"  # o legado False (= stacktrace) foi trocado antes da conferência


@pytest.mark.parametrize("detalhe", [False, "full", "stacktrace", "type"])
def test_demo_nao_entrega_traceback_ao_navegador(demo, monkeypatch, detalhe):
    def quebra(*_a, **_k):
        raise RuntimeError(f"falha com {HOST}")

    monkeypatch.setattr(tela, "texto_banner", quebra)  # exceção sem tratamento na página
    with _forcadas_pela_plataforma(), _opcao("client.showErrorDetails", detalhe):
        at = _app(demo).run()
    assert at.exception, "a exceção forçada devia ter acontecido"
    assert at.exception[0].stack_trace == []
    assert HOST not in at.exception[0].message


def test_demo_clique_duplo_nao_gera_dois_relatorios(demo, monkeypatch, relogio):
    geradas = []
    resultado = tela.RelatorioTela("concluído", "sucesso", "<p>a</p>", "{}\n", 0.01)
    monkeypatch.setattr(teto, "conferir_teto", lambda cfg: None)
    monkeypatch.setattr(tela, "gerar_para_tela", lambda *a, **k: geradas.append(1) or resultado)
    at = _app(demo).run()
    at = at.button[0].click().run()
    relogio.agora += tela.INTERVALO_GERACOES_S - 0.1  # o segundo clique chega logo depois
    at = at.button[0].click().run()
    assert len(geradas) == 1
    assert any("acabou de ser gerado" in t for t in _textos(at.info))
    relogio.agora += 0.1  # completou o intervalo
    at.button[0].click().run()
    assert len(geradas) == 2


def test_demo_teto_no_clique_ja_mostra_o_exemplo(demo, monkeypatch):
    at = _app(demo).run()
    monkeypatch.setattr(teto, "conferir_teto", lambda cfg: "A demonstração atingiu o limite.")
    at = at.button[0].click().run()
    assert any("exemplo" in e.label for e in at.expander)


def test_demo_sem_gatilho_no_banco_recusa(demo, monkeypatch):
    falta = [f"gatilho {teto.GATILHO_TETO} ausente ou desligado"]
    monkeypatch.setattr(teto, "prontidao_banco_demo", lambda cfg: falta)
    at = _app(demo).run()
    assert _textos(at.error) == [tela.MENSAGEM_DEMO_INDISPONIVEL]
    assert not at.tabs


def test_demo_banco_fora_nao_mostra_nem_registra_host_usuario_ou_senha(demo, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def banco_fora(cfg):
        raise psycopg.OperationalError(
            f'connection to server at "{HOST}", port 5432 failed: password authentication '
            f'failed for user "srag_auditor" ({SENHA})'
        )

    monkeypatch.setattr(teto, "prontidao_banco_demo", banco_fora)
    at = _app(demo).run()
    assert _textos(at.error) == [tela.MENSAGEM_DEMO_INDISPONIVEL]
    tela_toda, registrado = _todo_texto(at), caplog.text
    for proibido in (HOST, SENHA, '"srag_auditor"', "5432"):
        assert proibido not in tela_toda
        assert proibido not in registrado
    assert "OperationalError" in registrado  # o dono sabe o tipo da falha


def test_demo_configuracao_invalida_nao_lista_campos_para_o_visitante(demo, caplog):
    caplog.set_level(logging.INFO)
    segredos = {k: v for k, v in demo.items() if k != "PG_SENHA_AUDITOR"}
    at = _app(segredos).run()
    assert _textos(at.error) == [tela.MENSAGEM_DEMO_INDISPONIVEL]
    assert "pg_senha_auditor" not in _todo_texto(at)
    assert "pg_senha_auditor" in caplog.text


def test_demo_no_teto_desabilita_o_botao_e_o_dicionario_segue(demo, monkeypatch):
    monkeypatch.setattr(teto, "uso_de_hoje", lambda cfg: USO_NO_TETO)

    class Busca:
        metodo = "texto"

        def buscar(self, pergunta):
            return [TrechoDicionario(campo="VACINA_COV", pagina=7, texto="Recebeu vacina?",
                                     distancia=0.0)]  # fmt: skip

    @contextmanager
    def abrir_busca(cfg, embedder=None):
        assert embedder is None  # modo texto: a tela nem pede o modelo
        yield Busca()

    monkeypatch.setattr(consulta, "abrir_busca", abrir_busca)
    at = _app(demo).run()
    assert at.button[0].disabled  # "Gerar relatório"
    assert any("atingiu o limite de 10 relatórios" in t and "Renova à meia-noite" in t
               for t in _textos(at.info))  # fmt: skip
    at.text_input[0].input("o que é VACINA_COV?")
    at.button[1].click().run()
    assert any("VACINA_COV" in t and "nome exato" in t for t in _textos(at.markdown))
    assert any("Recebeu vacina?" in t for t in _textos(at.text))


def test_demo_uso_ilegivel_bloqueia_a_geracao(demo, monkeypatch):
    def fora(cfg):
        raise psycopg.OperationalError(f"server at {HOST} down")

    monkeypatch.setattr(teto, "uso_de_hoje", fora)
    at = _app(demo).run()
    assert at.button[0].disabled
    assert HOST not in _todo_texto(at)
    assert "indisponível" in _textos(at.info)[0]


def test_demo_clique_com_teto_atingido_entre_a_tela_e_o_clique_nao_gera(demo, monkeypatch):
    geradas = []
    monkeypatch.setattr(tela, "gerar_para_tela", lambda *a, **k: geradas.append(1))
    at = _app(demo).run()  # tela desenhada com uso livre (cache de exibição)
    monkeypatch.setattr(teto, "conferir_teto", lambda cfg: "A demonstração atingiu o limite.")
    at.button[0].click().run()
    assert geradas == []
    assert any("atingiu o limite" in t for t in _textos(at.info))


def test_demo_gera_sem_pergunta_e_confere_o_teto_antes(demo, monkeypatch):
    chamadas = []
    resultado = tela.RelatorioTela("concluído", "sucesso", "<p>a</p>", "{}\n", 0.01)

    def gerar(cfg, uf, pergunta, **k):
        chamadas.append(("gerar", uf, pergunta))
        return resultado

    monkeypatch.setattr(tela, "gerar_para_tela", gerar)
    monkeypatch.setattr(teto, "conferir_teto", lambda cfg: chamadas.append("conferir"))
    at = _app(demo).run()
    at.button[0].click().run()
    assert chamadas == ["conferir", ("gerar", "BR", None)]
    assert any("concluído" in t for t in _textos(at.success))


def test_entrada_da_plataforma_roda_a_mesma_tela(demo):
    at = _app(demo, ENTRADA_DEMO).run()
    assert not at.error and not at.exception
    assert any("Demonstração pública" in t for t in _textos(at.info))


class _BuscaContada:
    metodo = "texto"
    aberturas = 0

    def buscar(self, pergunta):
        return [TrechoDicionario(campo="VACINA_COV", pagina=7, texto="Recebeu vacina?",
                                 distancia=0.0)]  # fmt: skip


@pytest.fixture
def busca_contada(monkeypatch):
    busca = _BuscaContada()

    @contextmanager
    def abrir_busca(cfg, embedder=None):
        assert embedder is None  # modo texto: a tela nem pede o modelo
        busca.aberturas += 1
        yield busca

    monkeypatch.setattr(consulta, "abrir_busca", abrir_busca)
    return busca


def _consultar(at: AppTest, pergunta: str = "o que é VACINA_COV?") -> AppTest:
    at.text_input[-1].input(pergunta)  # o do dicionário é o último (local tem dois)
    return at.button[-1].click().run()


def test_demo_dicionario_respeita_o_intervalo_por_sessao(demo, relogio, busca_contada):
    at = _consultar(_app(demo).run())
    assert any("VACINA_COV" in t for t in _textos(at.markdown))
    relogio.agora += tela.INTERVALO_DICIONARIO_S - 0.1
    at = _consultar(at, "o que é UTI?")
    assert any("Aguarde" in t for t in _textos(at.warning))
    relogio.agora += 0.1  # agora completa o intervalo desde a última aceita
    at = _consultar(at, "o que é UTI?")
    assert not at.warning
    assert busca_contada.aberturas == 2


def test_demo_dicionario_teto_global_por_minuto(demo, relogio, busca_contada, monkeypatch):
    monkeypatch.setattr(tela, "CONSULTAS_DICIONARIO_POR_MINUTO", 1)
    _consultar(_app(demo).run())  # uma sessão usa a única vaga do minuto
    outra = _consultar(_app(demo).run(), "o que é UTI?")  # outra sessão, mesmo servidor
    assert any("neste minuto" in t for t in _textos(outra.warning))
    assert busca_contada.aberturas == 1


def test_demo_dicionario_repetido_vem_do_cache(demo, relogio, busca_contada):
    at = _consultar(_app(demo).run())
    relogio.agora += 10
    at = _consultar(at)
    assert any("VACINA_COV" in t for t in _textos(at.markdown))
    assert busca_contada.aberturas == 1


def test_local_dicionario_sem_limite_nem_cache(busca_contada, monkeypatch):
    monkeypatch.setattr(Config, "busca_dicionario", "texto", raising=False)

    def sem_modelo(*_a, **_k):
        raise AssertionError("o modo texto não pode carregar o modelo de embeddings")

    monkeypatch.setattr(embeddings, "criar_embedder", sem_modelo)
    at = _app().run()
    for _ in range(3):
        at = _consultar(at)
    assert not at.warning
    assert busca_contada.aberturas == 3  # local: comportamento de sempre


def test_demo_pasta_de_relatorios_nao_gravavel_recusa(demo, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    ocupado = tmp_path / "ocupado"
    ocupado.write_text("x")
    at = _app({**demo, "PASTA_RELATORIOS": str(ocupado / "relatorios")}).run()
    assert _textos(at.error) == [tela.MENSAGEM_DEMO_INDISPONIVEL]
    assert "pasta dos relatórios" in caplog.text


def test_demo_sem_pasta_configurada_usa_a_temporaria(demo, monkeypatch, tmp_path):
    monkeypatch.setattr(tela, "PASTA_RELATORIOS_DEMO", tmp_path / "temporaria")
    segredos = {k: v for k, v in demo.items() if k != "PASTA_RELATORIOS"}
    at = _app(segredos).run()
    assert not at.error and (tmp_path / "temporaria").is_dir()


def test_demo_no_teto_oferece_o_relatorio_de_exemplo(demo, monkeypatch):
    monkeypatch.setattr(teto, "uso_de_hoje", lambda cfg: USO_NO_TETO)
    at = _app(demo).run()
    assert any("exemplo" in e.label for e in at.expander)


def test_demo_livre_nao_mostra_o_exemplo(demo):
    at = _app(demo).run()
    assert not any("exemplo" in e.label for e in at.expander)
