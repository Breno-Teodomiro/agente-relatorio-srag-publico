"""Helpers da tela (sem Streamlit): geração pelo núcleo, nível da situação e leitura do HTML."""

import gc
import threading
import weakref
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from srag_agente import tela
from srag_agente.demo.teto import PacoteDemo, UsoDiario
from srag_agente.nucleo import ResultadoExecucao
from srag_agente.relatorio.montar import ArquivosRelatorio


def _resultado(tmp_path, status="ok") -> ResultadoExecucao:
    html = tmp_path / "relatorio.html"
    html.write_text("<html>relatório</html>", encoding="utf-8")
    auditoria = tmp_path / "auditoria.jsonl"
    auditoria.write_text('{"etapa": "nucleo"}\n', encoding="utf-8")
    arquivos = ArquivosRelatorio(pasta=tmp_path, html=html, markdown=tmp_path / "r.md",
                                 auditoria=auditoria)  # fmt: skip
    return ResultadoExecucao(id_execucao=uuid4(), status=status, arquivos=arquivos,
                             tokens_entrada=10, tokens_saida=5, custo_usd=0.0123)  # fmt: skip


IP_PUBLICO_FICTICIO = "93.184.216.34"


@pytest.fixture(autouse=True)
def _dns_ficticio(monkeypatch):
    """`host_local` resolve nomes; aqui todo nome resolve para um IP público fixo (sem rede)."""
    import srag_agente.demo as demo_pkg

    monkeypatch.setattr(demo_pkg.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", (IP_PUBLICO_FICTICIO, 0))])  # fmt: skip


LOCAL = SimpleNamespace(demo_online=False)
DEMO = SimpleNamespace(demo_online=True)


def test_gera_pelo_nucleo_com_uf_e_pergunta_sem_espacos_nas_pontas(tmp_path):
    chamadas = []

    def falso(cfg, *, uf, pergunta_extra, componentes):
        chamadas.append((uf, pergunta_extra, componentes))
        return _resultado(tmp_path)

    r = tela.gerar_para_tela(LOCAL, "SP", "  Como está a UTI? ", gerar=falso, componentes="c")
    assert chamadas == [("SP", "Como está a UTI?", "c")]
    assert r.html == "<html>relatório</html>"
    assert r.trilha == '{"etapa": "nucleo"}\n'
    assert r.custo_usd == 0.0123


def test_pergunta_em_branco_vira_sem_pergunta(tmp_path):
    chamadas = []

    def falso(cfg, *, uf, pergunta_extra, componentes):
        chamadas.append(pergunta_extra)
        return _resultado(tmp_path)

    tela.gerar_para_tela(LOCAL, "BR", "   ", gerar=falso, componentes=None)
    assert chamadas == [None]


@pytest.mark.parametrize(
    "status,nivel,rotulo",
    [("ok", "sucesso", "concluído"), ("parcial", "aviso", "parcial"),
     ("revisao_humana", "aviso", "revisão humana necessária"), ("erro", "erro", "erro")],
)  # fmt: skip
def test_situacao_tem_nivel_e_rotulo_em_portugues(tmp_path, status, nivel, rotulo):
    r = tela.gerar_para_tela(LOCAL, "BR", None, gerar=lambda *a, **k: _resultado(tmp_path, status),
                             componentes=None)  # fmt: skip
    assert (r.nivel, r.situacao) == (nivel, rotulo)


def test_url_isolada_leva_o_html_em_data_url_que_volta_igual():
    import base64

    url = tela.url_isolada("<p>relatório \u2014 açaí</p>")
    prefixo = "data:text/html;base64,"
    assert url.startswith(prefixo)
    assert base64.b64decode(url[len(prefixo) :]).decode("utf-8") == "<p>relatório \u2014 açaí</p>"


# --- trava global e teto por sessão (controle no servidor, não no botão) ---


def test_trava_ocupada_recusa_sem_executar():
    trava = threading.Lock()
    executou = []
    with trava, pytest.raises(tela.RelatorioEmAndamentoError):
        tela.executar_controlado(trava, {}, 5, lambda: executou.append(1))
    assert executou == []


def test_libera_a_trava_quando_a_geracao_falha():
    trava = threading.Lock()

    def falha():
        raise RuntimeError("banco fora")

    with pytest.raises(RuntimeError):
        tela.executar_controlado(trava, {}, 5, falha)
    assert trava.acquire(blocking=False)  # liberada: a próxima tentativa passa


def test_devolve_o_resultado_e_conta_o_uso_mesmo_quando_falha():
    trava, estado = threading.Lock(), {}
    assert tela.executar_controlado(trava, estado, 5, lambda: "ok") == "ok"
    with pytest.raises(RuntimeError):
        tela.executar_controlado(trava, estado, 5, lambda: (_ for _ in ()).throw(RuntimeError()))
    assert estado[tela.CHAVE_USADOS] == 2  # falha também gasta crédito: conta


def test_teto_por_sessao_estoura_sem_executar_nem_prender_a_trava():
    trava, estado = threading.Lock(), {tela.CHAVE_USADOS: 2}
    executou = []
    with pytest.raises(tela.TetoDeRelatoriosError):
        tela.executar_controlado(trava, estado, 2, lambda: executou.append(1))
    assert executou == []
    assert trava.acquire(blocking=False)
    assert tela.teto_atingido(estado, 2) and not tela.teto_atingido(estado, 3)


# --- resultado no servidor: sobrevive à execução interrompida pelo Streamlit ---


def test_resultado_e_guardado_mesmo_se_o_chamador_original_for_interrompido():
    trava, armazem = threading.Lock(), tela.ArmazemResultados(maximo=3)

    class Parada(BaseException):  # como a StopException do Streamlit
        pass

    def guardar(valor):
        armazem.guardar("s1", valor)
        raise Parada  # a escrita "do chamador" é interrompida depois de guardada

    with pytest.raises(Parada):
        tela.executar_controlado(trava, {}, 5, lambda: "relatório", guardar=guardar)
    assert armazem.ler("s1") == "relatório"
    assert trava.acquire(blocking=False)  # a trava saiu mesmo com BaseException


def test_libera_a_trava_com_baseexception_na_geracao():
    trava = threading.Lock()

    def interrompe():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        tela.executar_controlado(trava, {}, 5, interrompe)
    assert trava.acquire(blocking=False)


def test_erro_da_geracao_tambem_e_guardado_para_a_tela():
    trava, armazem = threading.Lock(), tela.ArmazemResultados(maximo=3)
    erro = RuntimeError("banco fora")

    def falha():
        raise erro

    with pytest.raises(RuntimeError):
        tela.executar_controlado(trava, {}, 5, falha, guardar=lambda v: armazem.guardar("s1", v))
    guardado = armazem.ler("s1")
    assert isinstance(guardado, tela.FalhaTela)
    assert "banco fora" in guardado.mensagem


def test_trava_ocupada_ou_teto_nao_guardam_nada():
    armazem = tela.ArmazemResultados(maximo=3)
    trava = threading.Lock()
    with trava, pytest.raises(tela.RelatorioEmAndamentoError):
        tela.executar_controlado(trava, {}, 5, lambda: "x",
                                 guardar=lambda v: armazem.guardar("s", v))  # fmt: skip
    with pytest.raises(tela.TetoDeRelatoriosError):
        tela.executar_controlado(trava, {tela.CHAVE_USADOS: 5}, 5, lambda: "x",
                                 guardar=lambda v: armazem.guardar("s", v))  # fmt: skip
    assert armazem.ler("s") is None


def test_armazem_guarda_so_o_ultimo_por_sessao_e_descarta_as_mais_antigas():
    armazem = tela.ArmazemResultados(maximo=2)
    armazem.guardar("a", 1)
    armazem.guardar("a", 2)  # substitui, não acumula
    armazem.guardar("b", 3)
    armazem.guardar("c", 4)  # estoura: sai a sessão mais antiga ("a")
    assert [armazem.ler(s) for s in "abc"] == [None, 3, 4]


def test_ler_renova_a_posicao_e_o_leitor_sobrevive_a_gravacoes_de_outras_sessoes():
    armazem = tela.ArmazemResultados(maximo=3)
    for sessao in ("leitor", "x", "y"):
        armazem.guardar(sessao, sessao)
    assert armazem.ler("leitor") == "leitor"  # renova: "x" passa a ser a mais antiga
    armazem.guardar("z", "z")
    armazem.guardar("w", "w")  # saem "x" e "y"
    assert [armazem.ler(s) for s in ("leitor", "x", "y")] == ["leitor", None, None]


def test_armazem_nao_guarda_a_excecao_nem_o_traceback():
    armazem = tela.ArmazemResultados(maximo=2)

    class Pergunta:  # variável local do frame que falhou: o traceback a manteria viva
        pass

    referencias = []

    def falha():
        pergunta = Pergunta()
        referencias.append(weakref.ref(pergunta))
        raise RuntimeError("banco fora")

    try:
        falha()
    except RuntimeError as erro:
        armazem.guardar("s", erro)
    gc.collect()
    assert referencias[0]() is None  # nada no armazém segura frames nem variáveis locais
    valor = armazem.ler("s")
    assert isinstance(valor, tela.FalhaTela) and not hasattr(valor, "__traceback__")


# --- falha antes do `guardar` (spinner, contador): a tela não pode mostrar o relatório antigo ---


class _EstadoQuebrado(dict):
    def __setitem__(self, chave, valor):
        raise RuntimeError("session_state fora")


def test_falha_antes_de_executar_substitui_o_resultado_antigo_pela_falha():
    armazem, trava = tela.ArmazemResultados(maximo=3), threading.Lock()
    armazem.guardar("s", "relatório antigo")
    tela.executar_e_guardar(armazem, "s", trava, _EstadoQuebrado(), 5, lambda: "novo")
    valor = armazem.ler("s")
    assert isinstance(valor, tela.FalhaTela)
    assert trava.acquire(blocking=False)


def test_executar_e_guardar_guarda_o_resultado_e_repassa_trava_e_teto():
    armazem, trava = tela.ArmazemResultados(maximo=3), threading.Lock()
    tela.executar_e_guardar(armazem, "s", trava, {}, 5, lambda: "ok")
    assert armazem.ler("s") == "ok"
    with trava, pytest.raises(tela.RelatorioEmAndamentoError):
        tela.executar_e_guardar(armazem, "s", trava, {}, 5, lambda: "x")
    with pytest.raises(tela.TetoDeRelatoriosError):
        tela.executar_e_guardar(armazem, "s", trava, {tela.CHAVE_USADOS: 5}, 5, lambda: "x")
    assert armazem.ler("s") == "ok"


# --- regressão: a tela nunca injeta HTML cru na página ---

_RAIZ_SRC = Path(__file__).resolve().parents[2] / "src"
_PROIBIDOS = ("unsafe_allow_html", "st.html(", "components.v1", "components.html")


def test_src_nao_injeta_html_cru_na_pagina():
    achados = []
    for arquivo in _RAIZ_SRC.rglob("*.py"):
        if arquivo.name == "test_tela.py":
            continue
        for n, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
            if linha.lstrip().startswith("#"):
                continue
            if any(p in linha for p in _PROIBIDOS):
                achados.append(f"{arquivo.name}:{n}: {linha.strip()}")
            # st.iframe só com a URL `data:` de url_isolada: HTML cru iria para srcdoc
            # com allow-same-origin e alcançaria a página.
            if "st.iframe(" in linha and "url_isolada(" not in linha:
                achados.append(f"{arquivo.name}:{n}: {linha.strip()}")
    assert achados == []


# --- demonstração: pergunta complementar desligada também no servidor ---


def test_demo_nunca_repassa_a_pergunta_ao_nucleo(tmp_path):
    chamadas = []

    def falso(cfg, *, uf, pergunta_extra, componentes, hoje=None):
        chamadas.append(pergunta_extra)
        return _resultado(tmp_path)

    tela.gerar_para_tela(DEMO, "SP", "minha mãe Fulana, internada", gerar=falso, componentes=None)
    assert chamadas == [None]


# --- teto global diário (demonstração), conferido dentro da trava ---


def _nunca():
    raise AssertionError("não devia executar")


def test_teto_global_recusa_sem_executar_sem_contar_e_solta_a_trava():
    trava, estado = threading.Lock(), {tela.CHAVE_USADOS: 1}
    with pytest.raises(tela.TetoGlobalError) as aviso:
        tela.executar_controlado(trava, estado, 5, _nunca,
                                 conferir_global=lambda: "limite de hoje")  # fmt: skip
    assert str(aviso.value) == "limite de hoje"
    assert estado[tela.CHAVE_USADOS] == 1  # nada foi gasto: a sessão não perde a tentativa
    assert trava.acquire(blocking=False)


def test_conferencia_global_acontece_com_a_trava_pega():
    trava, vistas = threading.Lock(), []

    def conferir():
        vistas.append(trava.locked())
        return None

    assert tela.executar_controlado(trava, {}, 5, lambda: "ok", conferir_global=conferir) == "ok"
    assert vistas == [True]


def test_conferencia_global_antes_de_contar_e_de_executar():
    trava, estado, ordem = threading.Lock(), {}, []

    def conferir():
        ordem.append(("conferir", estado.get(tela.CHAVE_USADOS, 0)))

    def executar():
        ordem.append(("executar", estado[tela.CHAVE_USADOS]))
        return "ok"

    tela.executar_controlado(trava, estado, 5, executar, conferir_global=conferir)
    assert ordem == [("conferir", 0), ("executar", 1)]


def test_falha_ao_ler_o_uso_nao_executa_nao_conta_e_solta_a_trava():
    trava, estado = threading.Lock(), {}

    def falha():
        raise RuntimeError("banco fora")

    with pytest.raises(RuntimeError):
        tela.executar_controlado(trava, estado, 5, _nunca, conferir_global=falha)
    assert estado.get(tela.CHAVE_USADOS, 0) == 0
    assert trava.acquire(blocking=False)


def test_baseexception_na_conferencia_tambem_solta_a_trava():
    trava = threading.Lock()

    def interrompe():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        tela.executar_controlado(trava, {}, 5, _nunca, conferir_global=interrompe)
    assert trava.acquire(blocking=False)


def test_sem_conferir_banco_quando_a_sessao_ou_a_trava_ja_recusam():
    chamadas = []
    trava = threading.Lock()
    with pytest.raises(tela.TetoDeRelatoriosError):
        tela.executar_controlado(trava, {tela.CHAVE_USADOS: 5}, 5, _nunca,
                                 conferir_global=lambda: chamadas.append(1))  # fmt: skip
    with trava, pytest.raises(tela.RelatorioEmAndamentoError):
        tela.executar_controlado(trava, {}, 5, _nunca,
                                 conferir_global=lambda: chamadas.append(1))  # fmt: skip
    assert chamadas == []


def test_duas_threads_com_um_relatorio_restante_geram_um_so():
    # Dublê do banco: um relatório cabe; a conferência e o "começar" ficam sob a mesma trava.
    trava, usados, geradas = threading.Lock(), [0], []
    pronto = threading.Barrier(2)

    def conferir():
        return None if usados[0] < 1 else "limite"

    def gerar():
        usados[0] += 1
        geradas.append(1)
        return "ok"

    erros: list[BaseException] = []

    def visitante():
        pronto.wait()
        for _ in range(200):  # insiste até a trava liberar
            try:
                tela.executar_controlado(trava, {}, 5, gerar, conferir_global=conferir)
                return
            except tela.RelatorioEmAndamentoError:
                continue
            except tela.TetoGlobalError as erro:
                erros.append(erro)
                return

    threads = [threading.Thread(target=visitante) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(geradas) == 1 and len(erros) == 1


def test_executar_e_guardar_repassa_o_teto_global_sem_guardar():
    armazem, trava = tela.ArmazemResultados(maximo=3), threading.Lock()
    armazem.guardar("s", "relatório anterior")
    with pytest.raises(tela.TetoGlobalError):
        tela.executar_e_guardar(armazem, "s", trava, {}, 5, _nunca,
                                conferir_global=lambda: "limite")  # fmt: skip
    assert armazem.ler("s") == "relatório anterior"
    assert trava.acquire(blocking=False)


def test_executar_e_guardar_falha_de_leitura_vira_resultado_da_sessao():
    armazem, trava = tela.ArmazemResultados(maximo=3), threading.Lock()

    def falha():
        raise RuntimeError("banco fora")

    tela.executar_e_guardar(armazem, "s", trava, {}, 5, _nunca, conferir_global=falha)
    assert isinstance(armazem.ler("s"), tela.FalhaTela)


def test_armazem_publico_guarda_mensagem_sem_detalhe():
    armazem = tela.ArmazemResultados(maximo=3, publico=True)
    armazem.guardar("s", RuntimeError("ep-canario.neon.tech senha=xyz"))
    mensagem = armazem.ler("s").mensagem
    assert "canario" not in mensagem and "xyz" not in mensagem and "Referência" in mensagem


# --- segredos da plataforma -> Config ---


def test_segredos_so_campos_do_config_sem_tabela_aninhada():
    segredos = {
        "PG_HOST": "ep-x.neon.tech",
        "pg_sslmode": "require",
        "DEMO_ONLINE": "1",
        "DESCONHECIDA": "x",
        "_env_file": "/etc/outro.env",  # não pode redirecionar o arquivo lido pelo Config
        "connections": {"pg_host": "aninhado"},
        "pg_banco": {"aninhado": "1"},
    }
    assert tela.config_dos_segredos(segredos) == {
        "pg_host": "ep-x.neon.tech",
        "pg_sslmode": "require",
        "demo_online": "1",
    }


def test_segredos_vazios_nao_mudam_nada():
    assert tela.config_dos_segredos({}) == {}


@pytest.mark.parametrize(
    ("segredos", "ambiente", "pedido"),
    [
        ({}, {}, False),
        ({"DEMO_ONLINE": "1"}, {}, True),
        ({"demo_online": True}, {}, True),
        ({}, {"DEMO_ONLINE": "true"}, True),
        ({}, {"demo_online": "yes"}, True),
        ({"DEMO_ONLINE": "talvez"}, {}, True),  # valor estranho: demo (mensagem genérica)
        ({"DEMO_ONLINE": "0"}, {}, False),
        ({"DEMO_ONLINE": False}, {}, False),
        ({}, {"DEMO_ONLINE": "off"}, False),
        ({"DEMO_ONLINE": "0"}, {"DEMO_ONLINE": "1"}, True),  # qualquer fonte pedindo basta
        ({"DEMO_ONLINE": {"x": 1}}, {}, False),
    ],
)
def test_demo_pedido(segredos, ambiente, pedido):
    assert tela.demo_pedido(segredos, ambiente) is pedido


# --- ambiente do modo demo: tudo conferido na partida, falha fechada ---


def _cfg_demo(**campos):
    from pydantic import SecretStr

    base = {
        "demo_online": True,
        "pg_host": "ep-x.sa-east-1.aws.neon.tech",
        "pg_sslmode": "verify-full",
        "pg_senha_admin": None,
        "pg_senha_etl": None,
        "pg_senha_agente": SecretStr("a"),
        "pg_senha_auditor": SecretStr("b"),
        "llm_provider": "openrouter",
        "openrouter_api_key": SecretStr("k"),
        "openai_api_key": None,
        "langfuse_public_key": None,
        "langfuse_secret_key": None,
    }
    base.update(campos)
    return SimpleNamespace(**base)


def test_ambiente_demo_correto_nao_tem_problema():
    assert tela.problemas_ambiente_demo(_cfg_demo()) == []


@pytest.mark.parametrize(
    "host",
    ["localhost", "LOCALHOST.", "127.0.0.1", "127.0.0.2", "::1", "banco", "host.docker.internal",
     "maquina.local", "0.0.0.0", "", "/var/run/postgresql", "ep-x.neon.tech,localhost",
     "app.localhost", "10.0.0.5", "192.168.1.10", "::ffff:127.0.0.1", "[::1]"],
)  # fmt: skip
def test_ambiente_demo_recusa_host_local(host):
    problemas = tela.problemas_ambiente_demo(_cfg_demo(pg_host=host))
    assert any("PG_HOST" in p for p in problemas)
    assert all(host not in p for p in problemas if host)  # o valor não vai para o log


@pytest.mark.parametrize("modo", [None, "disable", "allow", "prefer"])
def test_ambiente_demo_recusa_tls_fraco(modo):
    assert any("PG_SSLMODE" in p for p in tela.problemas_ambiente_demo(_cfg_demo(pg_sslmode=modo)))


@pytest.mark.parametrize("modo", ["require", "verify-ca", "verify-full"])
def test_ambiente_demo_aceita_tls(modo):
    assert tela.problemas_ambiente_demo(_cfg_demo(pg_sslmode=modo)) == []


@pytest.mark.parametrize(
    ("campo", "trecho"),
    [
        ("pg_senha_admin", "PG_SENHA_ADMIN"),
        ("pg_senha_etl", "PG_SENHA_ETL"),
        ("openai_api_key", "OPENAI_API_KEY"),
        ("langfuse_public_key", "LANGFUSE"),
        ("langfuse_secret_key", "LANGFUSE"),
    ],
)
def test_ambiente_demo_recusa_credencial_que_nao_e_dela(campo, trecho):
    from pydantic import SecretStr

    segredo = "valor-canario-123"
    problemas = tela.problemas_ambiente_demo(_cfg_demo(**{campo: SecretStr(segredo)}))
    assert any(trecho in p for p in problemas)
    assert all(segredo not in p for p in problemas)


def test_ambiente_demo_exige_a_chave_do_openrouter():
    assert tela.problemas_ambiente_demo(_cfg_demo(openrouter_api_key=None))
    assert tela.problemas_ambiente_demo(_cfg_demo(llm_provider="openai"))


_OPCOES_OK = {
    "client.showErrorDetails": "none",
    "server.enableXsrfProtection": True,
    "server.enableCORS": True,
    "client.toolbarMode": "viewer",
    "server.enableStaticServing": False,
}
# O que a plataforma de hospedagem força por cima do config.toml (documentação dela): não é
# controle de segurança da página e não pode impedir a demonstração de abrir.
_FORCADAS_PELA_PLATAFORMA = {
    "server.runOnSave": True,
    "browser.gatherUsageStats": True,
    "runner.fastReruns": True,
    "server.enableXsrfProtection": True,
}


def test_opcoes_do_streamlit_corretas():
    assert tela.problemas_opcoes_demo(_OPCOES_OK.__getitem__) == []


def test_valores_forcados_pela_plataforma_nao_bloqueiam():
    opcoes = {**_OPCOES_OK, **_FORCADAS_PELA_PLATAFORMA}
    assert tela.problemas_opcoes_demo(opcoes.__getitem__) == []


def test_so_as_opcoes_de_seguranca_sao_conferidas():
    assert set(tela.OPCOES_EXIGIDAS_DEMO) | {"client.showErrorDetails"} == set(_OPCOES_OK)


@pytest.mark.parametrize(
    ("opcao", "valor"),
    [
        ("client.showErrorDetails", "full"),
        ("client.showErrorDetails", True),
        ("client.showErrorDetails", "stacktrace"),
        ("client.showErrorDetails", "type"),
        ("client.showErrorDetails", "None"),
        ("client.showErrorDetails", False),  # legado: equivale a "stacktrace" no Streamlit
        ("client.showErrorDetails", "false"),
        ("client.showErrorDetails", 0),  # 0 == False, mas não é o booleano
        ("server.enableXsrfProtection", False),
        ("server.enableCORS", False),
        ("server.enableCORS", 1),  # 1 == True, mas não é o booleano esperado
        ("client.toolbarMode", "developer"),
        ("client.toolbarMode", "auto"),
        ("server.enableStaticServing", True),
    ],
)
def test_cada_opcao_relaxada_recusa_a_demo(opcao, valor):
    opcoes = {**_OPCOES_OK, opcao: valor}
    problemas = tela.problemas_opcoes_demo(opcoes.__getitem__)
    assert problemas == [f"{opcao} diferente do exigido"]  # só o nome, nunca o valor


@pytest.mark.parametrize("opcao", sorted(_OPCOES_OK))
def test_opcao_ilegivel_recusa_a_demo(opcao):
    def ler(nome):
        if nome == opcao:
            raise RuntimeError("opção desconhecida")
        return _OPCOES_OK[nome]

    assert tela.problemas_opcoes_demo(ler) == [f"{opcao} ilegível"]


# --- textos da demonstração ---

_USO = UsoDiario(3, 0, 0.1, 4, 10, 40, 1.0, datetime(2026, 10, 19, 3, tzinfo=UTC))
_PACOTE = PacoteDemo("0123456789abcdef" * 4, "ded6a6e", date(2025, 6, 25))


def test_banner_com_uso_e_data_de_referencia():
    texto = tela.texto_banner(_USO, _PACOTE)
    assert "25/06/2025" in texto and "hoje: 3 de 10" in texto
    assert "pergunta complementar" in texto.lower()


def test_banner_sem_uso_nem_pacote_nao_quebra():
    texto = tela.texto_banner(None, None)
    assert "indisponível" in texto


def test_rodape_mostra_proveniencia_e_a_busca():
    texto = tela.texto_rodape(_PACOTE, "texto")
    assert "0123456789ab" in texto and "ded6a6e" in texto and "texto completo" in texto
    assert "similaridade" in tela.texto_rodape(_PACOTE, "vetor")


def test_rodape_nao_repassa_markdown_vindo_do_banco():
    estranho = PacoteDemo("[x](javascript:alert(1))", "**v** <b>", None)
    texto = tela.texto_rodape(estranho, "texto")
    for trecho in ("[x]", "javascript", "**v**", "<b>"):
        assert trecho not in texto
    assert "desconhecido" in texto  # sha256 que não é hexadecimal não aparece


# --- prova de ponta a ponta: o texto da pergunta não chega ao núcleo nem à trilha na demo ---


def _rodar_nucleo_pela_tela(tmp_path, demo_online: bool, pergunta: str):
    """`gerar_para_tela` -> núcleo real (`executar_relatorio`) com auditoria em memória e dublês."""
    import json

    from srag_agente.auditoria.registro import Auditoria
    from srag_agente.config import Config
    from srag_agente.nucleo import executar_relatorio
    from tests.fakes import HOJE, DicionarioFalso, RepositorioFalso
    from tests.unit.test_nucleo import _componentes

    cfg = Config(_env_file=None, demo_online=demo_online, pg_senha_admin="a", pg_senha_etl="e",
                 pg_senha_agente="g", pg_senha_auditor="u", pasta_relatorios=tmp_path)  # fmt: skip
    auditoria, recebidas = Auditoria(), []

    def gerar(cfg, *, uf, pergunta_extra, componentes, hoje=None):
        recebidas.append(pergunta_extra)
        return executar_relatorio(cfg, auditoria=auditoria, repo=RepositorioFalso(),
                                  dicionario=DicionarioFalso(), componentes=_componentes(), uf=uf,
                                  pergunta_extra=pergunta_extra, hoje=HOJE)  # fmt: skip

    r = tela.gerar_para_tela(cfg, "BR", pergunta, gerar=gerar, componentes=None)
    trilha = json.dumps([auditoria.execucao, auditoria.eventos], default=str, ensure_ascii=False)
    return recebidas, trilha, r


CANARIO = "palavracanario"  # qualquer texto que o visitante digitasse


def test_demo_pergunta_nao_chega_ao_nucleo_nem_a_trilha_nem_ao_relatorio(tmp_path):
    recebidas, trilha, r = _rodar_nucleo_pela_tela(tmp_path, True, f"Como está {CANARIO}?")
    assert recebidas == [None]
    assert CANARIO not in trilha  # parametros.pergunta_extra e eventos
    assert CANARIO not in r.html and CANARIO not in r.trilha  # relatório e JSONL baixável


def test_local_controle_a_mesma_pergunta_aparece_na_trilha(tmp_path):
    # sem isto, o teste acima passaria mesmo se a busca pelo canário olhasse o lugar errado
    recebidas, trilha, _ = _rodar_nucleo_pela_tela(tmp_path, False, f"Como está {CANARIO}?")
    assert recebidas == [f"Como está {CANARIO}?"]
    assert CANARIO in trilha


# --- data do relatório no fuso de Brasília (o teto conta o dia de lá) ---


@pytest.mark.parametrize(
    ("agora_utc", "dia"),
    [
        (datetime(2026, 10, 19, 2, 59, tzinfo=UTC), date(2026, 10, 18)),  # 23h59 em Brasília
        (datetime(2026, 10, 19, 3, 0, tzinfo=UTC), date(2026, 10, 19)),  # meia-noite
        (datetime(2026, 10, 19, 1, 0, tzinfo=UTC), date(2026, 10, 18)),  # 22h: UTC já virou
    ],
)
def test_hoje_da_demo_e_o_dia_de_brasilia(agora_utc, dia):
    assert tela.hoje_demo(agora_utc) == dia


def test_demo_passa_o_dia_de_brasilia_ao_nucleo_e_local_nao(tmp_path, monkeypatch):
    recebidos = []

    def falso(cfg, **k):
        recebidos.append(k.get("hoje", "ausente"))
        return _resultado(tmp_path)

    monkeypatch.setattr(tela, "hoje_demo", lambda: date(2026, 10, 18))
    tela.gerar_para_tela(DEMO, "BR", None, gerar=falso, componentes=None)
    tela.gerar_para_tela(LOCAL, "BR", None, gerar=falso, componentes=None)
    assert recebidos == [date(2026, 10, 18), "ausente"]  # local: o núcleo decide, como sempre


# --- pasta dos relatórios na demonstração ---


def _config_real(**campos):
    from srag_agente.config import Config

    return Config(_env_file=None, pg_senha_agente="g", pg_senha_auditor="u", **campos)


def test_demo_sem_pasta_configurada_usa_a_pasta_temporaria():
    import tempfile

    cfg = tela.ajustar_config_demo(_config_real(demo_online=True))
    assert cfg.pasta_relatorios == Path(tempfile.gettempdir()) / "srag-relatorios"


def test_demo_respeita_pasta_configurada(tmp_path):
    cfg = tela.ajustar_config_demo(_config_real(demo_online=True, pasta_relatorios=tmp_path))
    assert cfg.pasta_relatorios == tmp_path


def test_local_nao_muda_a_pasta():
    cfg = _config_real(pg_senha_admin="a", pg_senha_etl="e")
    assert tela.ajustar_config_demo(cfg) is cfg


def test_pasta_gravavel_e_criada(tmp_path):
    pasta = tmp_path / "nova" / "relatorios"
    assert tela.problema_pasta(pasta) is None
    assert pasta.is_dir() and list(pasta.iterdir()) == []  # a sonda não deixa arquivo


def test_pasta_nao_gravavel_e_problema_sem_o_caminho(tmp_path):
    arquivo = tmp_path / "ocupado"
    arquivo.write_text("x")  # um arquivo no lugar da pasta: não dá para criar nem gravar
    problema = tela.problema_pasta(arquivo / "relatorios")
    assert problema is not None and str(tmp_path) not in problema


# --- dicionário: intervalo por sessão e teto global por minuto ---


@pytest.mark.parametrize(("passados", "pode"), [(2.9, False), (3.0, True), (3.1, True)])
def test_intervalo_minimo_por_sessao(passados, pode):
    estado = {}
    assert tela.intervalo_respeitado(estado, 100.0, 3.0)  # a primeira sempre pode
    assert tela.intervalo_respeitado(estado, 100.0 + passados, 3.0) is pode


def test_intervalo_recusado_nao_empurra_a_janela():
    estado = {}
    tela.intervalo_respeitado(estado, 100.0, 3.0)
    assert not tela.intervalo_respeitado(estado, 102.0, 3.0)
    assert tela.intervalo_respeitado(estado, 103.0, 3.0)  # conta da última aceita, não da recusada


class _Relogio:
    def __init__(self):
        self.agora = 1000.0

    def __call__(self):
        return self.agora


def test_teto_global_por_minuto_na_fronteira():
    relogio = _Relogio()
    limite = tela.LimiteConsultas(3, relogio=relogio)
    assert [limite.permitir() for _ in range(3)] == [True, True, True]  # igual ao teto: cabe
    assert not limite.permitir()  # um acima
    relogio.agora += 59.9
    assert not limite.permitir()  # a janela ainda não andou
    relogio.agora += 0.1  # 60 s depois das três primeiras
    assert limite.permitir()


def test_teto_global_por_minuto_recusa_nao_ocupa_vaga():
    relogio = _Relogio()
    limite = tela.LimiteConsultas(1, relogio=relogio)
    assert limite.permitir()
    for _ in range(5):
        relogio.agora += 10
        assert not limite.permitir()
    relogio.agora = 1060.0
    assert limite.permitir()


def test_teto_global_por_minuto_entre_threads():
    limite = tela.LimiteConsultas(50, relogio=lambda: 0.0)
    aceitas = []

    def consultar():
        for _ in range(20):
            if limite.permitir():
                aceitas.append(1)

    threads = [threading.Thread(target=consultar) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(aceitas) == 50


# --- relatório de exemplo quando o teto acaba ---


def test_exemplo_versionado_e_lido():
    from srag_agente.config import RAIZ_PROJETO

    html = tela.ler_exemplo(RAIZ_PROJETO / tela.EXEMPLO_RELATORIO)
    assert html is not None and "<html" in html.lower()


def test_exemplo_ausente_vira_none(tmp_path):
    assert tela.ler_exemplo(tmp_path / "nada.html") is None


@pytest.mark.skipif(not hasattr(__import__("os"), "geteuid") or __import__("os").geteuid() == 0,
                    reason="root grava em pasta sem permissão")  # fmt: skip
def test_pasta_existente_sem_permissao_de_escrita_e_problema(tmp_path):
    pasta = tmp_path / "somente_leitura"
    pasta.mkdir()
    pasta.chmod(0o500)
    try:
        assert tela.problema_pasta(pasta) is not None
    finally:
        pasta.chmod(0o700)


def test_ambiente_demo_recusa_nome_que_nao_resolve_ou_resolve_para_rede_interna(monkeypatch):
    import srag_agente.demo as demo_pkg

    def sem_dns(*_a, **_k):
        raise OSError("nome desconhecido")

    monkeypatch.setattr(demo_pkg.socket, "getaddrinfo", sem_dns)
    assert any("PG_HOST" in p for p in tela.problemas_ambiente_demo(_cfg_demo()))
    monkeypatch.setattr(demo_pkg.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 0))])  # fmt: skip
    assert any("PG_HOST" in p for p in tela.problemas_ambiente_demo(_cfg_demo()))


# --- (3) intervalo mínimo entre gerações da mesma sessão (clique duplo) ---


def _gerar_contado(contador):
    def executar():
        contador.append(1)
        return "ok"

    return executar


@pytest.mark.parametrize(("passados", "gera"), [(19.9, False), (20.0, True), (20.1, True)])
def test_intervalo_entre_geracoes_da_mesma_sessao(passados, gera):
    armazem, trava, estado, feitas = tela.ArmazemResultados(), threading.Lock(), {}, []
    tela.executar_e_guardar(armazem, "s", trava, estado, 10, _gerar_contado(feitas),
                            intervalo=20.0, relogio=lambda: 100.0)  # fmt: skip
    chamada = lambda: tela.executar_e_guardar(  # noqa: E731
        armazem, "s", trava, estado, 10, _gerar_contado(feitas),
        intervalo=20.0, relogio=lambda: 100.0 + passados)  # fmt: skip
    if gera:
        chamada()
        assert len(feitas) == 2 and estado[tela.CHAVE_USADOS] == 2
    else:
        with pytest.raises(tela.GeracaoRecenteError):
            chamada()
        assert len(feitas) == 1 and estado[tela.CHAVE_USADOS] == 1  # nem gera nem conta
    assert trava.acquire(blocking=False)


def test_intervalo_conta_do_fim_mesmo_quando_a_geracao_falha():
    armazem, trava, estado = tela.ArmazemResultados(), threading.Lock(), {}

    def falha():
        raise RuntimeError("banco fora")

    tela.executar_e_guardar(armazem, "s", trava, estado, 10, falha, intervalo=20.0,
                            relogio=lambda: 100.0)  # fmt: skip
    with pytest.raises(tela.GeracaoRecenteError):
        tela.executar_e_guardar(armazem, "s", trava, estado, 10, _nunca, intervalo=20.0,
                                relogio=lambda: 105.0)  # fmt: skip


def test_intervalo_e_por_sessao_e_desligado_por_padrao():
    armazem, trava, feitas = tela.ArmazemResultados(), threading.Lock(), []
    tela.executar_e_guardar(armazem, "a", trava, {}, 10, _gerar_contado(feitas), intervalo=20.0,
                            relogio=lambda: 100.0)  # fmt: skip
    # outra sessão gera mesmo dentro do intervalo da primeira
    tela.executar_e_guardar(armazem, "b", trava, {}, 10, _gerar_contado(feitas), intervalo=20.0,
                            relogio=lambda: 101.0)  # fmt: skip
    tela.executar_e_guardar(armazem, "a", trava, {}, 10, _gerar_contado(feitas))  # local: sem
    assert len(feitas) == 3
