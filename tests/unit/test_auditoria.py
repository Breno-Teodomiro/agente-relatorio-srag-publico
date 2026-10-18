import json
import subprocess
import threading
import time
from datetime import date

import pytest

from srag_agente.auditoria import versao
from srag_agente.auditoria.registro import LIMITE_RESULTADO, Auditoria, resumir


def _auditoria() -> Auditoria:
    a = Auditoria()
    a.iniciar_execucao(
        provedor="openrouter",
        modelo="openai/gpt-4.1-mini",
        parametros={"uf": "BR"},
        versao_codigo="abc123",
        hash_prompts="h1",
    )
    return a


def _finalizar(a: Auditoria, status: str = "ok", **extra) -> None:
    a.finalizar_execucao(
        status=status,
        data_referencia=date(2025, 6, 25),
        tokens_entrada=100,
        tokens_saida=20,
        custo_usd=0.0000721,
        **extra,
    )


def _linhas(caminho) -> list[dict]:
    return [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines()]


# --- Do plano --------------------------------------------------------------------------------


def test_eventos_recebem_sequencia_e_guardam_o_motivo():
    a = _auditoria()
    a.registrar(
        "ferramentas",
        "ferramenta",
        ferramenta="consultar_metricas",
        motivo="preciso da mortalidade",
        resultado={"valor": 0.1},
    )
    a.registrar("guardrail_saida", "guardrail", resultado={"aprovado": True})
    assert [e["sequencia"] for e in a.eventos] == [1, 2]
    assert a.eventos[0]["motivo"] == "preciso da mortalidade"


def test_resultado_grande_e_truncado():
    a = _auditoria()
    evento = a.registrar("ferramentas", "ferramenta", resultado={"texto": "x" * 10_000})
    assert len(evento["resultado"]["resumo_truncado"]) == LIMITE_RESULTADO
    assert evento["resultado"]["tamanho_original"] > LIMITE_RESULTADO


def test_jsonl_tem_execucao_e_eventos(tmp_path):
    a = _auditoria()
    a.registrar("orquestrador", "llm", tokens_entrada=100, tokens_saida=20)
    _finalizar(a)
    linhas = _linhas(a.gravar_jsonl(tmp_path / "auditoria.jsonl"))
    assert linhas[0]["tipo_registro"] == "execucao" and linhas[0]["status"] == "ok"
    assert linhas[1]["tipo_registro"] == "evento" and linhas[1]["no"] == "orquestrador"
    assert linhas[1]["id_execucao"] == linhas[0]["id_execucao"]


# --- Ciclo de vida: a trilha só cresce enquanto a execução está aberta -----------------------


def test_nao_registra_antes_de_iniciar():
    with pytest.raises(RuntimeError, match="iniciar_execucao"):
        Auditoria().registrar("orquestrador", "llm")


def test_nao_inicia_duas_vezes():
    a = _auditoria()
    with pytest.raises(RuntimeError, match="já iniciada"):
        a.iniciar_execucao(
            provedor="p", modelo="m", parametros={}, versao_codigo="v", hash_prompts="h"
        )


def test_nao_registra_depois_de_finalizar():
    a = _auditoria()
    _finalizar(a)
    with pytest.raises(RuntimeError, match="encerrada"):
        a.registrar("orquestrador", "llm")
    assert a.eventos == []


def test_nao_finaliza_duas_vezes():
    a = _auditoria()
    _finalizar(a, status="parcial")
    with pytest.raises(RuntimeError, match="encerrada"):
        _finalizar(a, status="ok")
    assert a.execucao["status"] == "parcial"


@pytest.mark.parametrize("status", ["em_andamento", "inventado", ""])
def test_finalizar_exige_status_final(status):
    a = _auditoria()
    with pytest.raises(ValueError, match="status"):
        _finalizar(a, status=status)
    assert a.execucao["status"] == "em_andamento" and "fim" not in a.execucao


@pytest.mark.parametrize(
    "extra",
    [{"tokens_entrada": -1}, {"tokens_saida": -1}, {"custo_usd": -0.01},
     {"custo_usd": float("nan")}, {"total_casos": -5}],
)  # fmt: skip
def test_finalizar_recusa_consumo_impossivel(extra):
    a = _auditoria()
    valores = {"tokens_entrada": 1, "tokens_saida": 1, "custo_usd": 0.0} | extra
    with pytest.raises(ValueError):
        a.finalizar_execucao(status="ok", data_referencia=None, **valores)
    assert "fim" not in a.execucao


def test_finalizar_guarda_total_casos_e_data(tmp_path):
    a = _auditoria()
    _finalizar(a, total_casos=165_397)
    execucao = _linhas(a.gravar_jsonl(tmp_path / "a.jsonl"))[0]
    assert execucao["total_casos"] == 165_397
    assert execucao["data_referencia"] == "2025-06-25"
    assert execucao["custo_usd"] == pytest.approx(0.000072)
    assert execucao["fim"] is not None


def test_falha_ao_persistir_nao_deixa_evento_fantasma():
    class FalhaUmaVez(Auditoria):
        falhou = False

        def _persistir_evento(self, evento):
            if not self.falhou:
                self.falhou = True
                raise ConnectionError("banco caiu")
            return {}

    a = FalhaUmaVez()
    a.iniciar_execucao(provedor="p", modelo="m", parametros={}, versao_codigo="v", hash_prompts="h")
    with pytest.raises(ConnectionError):
        a.registrar("orquestrador", "llm")
    assert a.eventos == []
    # A nova tentativa reaproveita a sequência: a trilha não fica com buraco.
    assert a.registrar("orquestrador", "llm")["sequencia"] == 1


def test_falha_ao_persistir_inicio_deixa_a_auditoria_limpa():
    class SemBanco(Auditoria):
        def _persistir_inicio(self):
            raise ConnectionError("banco caiu")

    a = SemBanco()
    with pytest.raises(ConnectionError):
        a.iniciar_execucao(
            provedor="p", modelo="m", parametros={}, versao_codigo="v", hash_prompts="h"
        )
    assert a.id_execucao is None and a.execucao == {}


def test_registros_simultaneos_nao_repetem_sequencia():
    class GravacaoLenta(Auditoria):
        # Gravar leva tempo (no banco, uma ida e volta): é a janela entre ler a sequência e
        # guardar o evento. Sem ela, a corrida quase nunca aparece e o teste não prova a trava.
        def _persistir_evento(self, evento):
            time.sleep(0.001)
            return {}

    a = GravacaoLenta()
    a.iniciar_execucao(provedor="p", modelo="m", parametros={}, versao_codigo="v", hash_prompts="h")

    def registrar_varios():
        for _ in range(25):
            a.registrar("ferramentas", "ferramenta")

    threads = [threading.Thread(target=registrar_varios) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(e["sequencia"] for e in a.eventos) == list(range(1, 201))


# --- Conteúdo: pequeno, serializável e sem estourar o banco ----------------------------------


def test_resumir_mantem_valor_pequeno():
    assert resumir({"a": [1, 2], "b": None}) == {"a": [1, 2], "b": None}
    assert resumir(None) is None
    assert resumir(date(2025, 6, 25)) == "2025-06-25"


def test_resumir_troca_numero_nao_finito_por_texto():
    # NaN/Infinity não existem em JSON; o jsonb do Postgres recusaria o evento inteiro.
    assert resumir({"taxa": float("nan"), "razao": float("inf")}) == {
        "taxa": "nan",
        "razao": "inf",
    }


def test_motivo_e_erro_longos_sao_cortados():
    a = _auditoria()
    evento = a.registrar("ferramentas", "decisao", motivo="m" * 5_000, erro="e" * 5_000)
    assert len(evento["motivo"]) == LIMITE_RESULTADO
    assert len(evento["erro"]) == LIMITE_RESULTADO


def test_jsonl_e_json_valido_mesmo_com_nan(tmp_path):
    a = _auditoria()
    a.registrar("ferramentas", "ferramenta", resultado={"taxa": float("nan")})
    texto = a.gravar_jsonl(tmp_path / "a.jsonl").read_text(encoding="utf-8")
    assert "NaN" not in texto
    for linha in texto.splitlines():
        json.loads(linha, parse_constant=lambda c: pytest.fail(f"constante {c} no JSONL"))


def test_jsonl_sem_eventos_tem_so_a_execucao(tmp_path):
    a = _auditoria()
    _finalizar(a)
    linhas = _linhas(a.gravar_jsonl(tmp_path / "sub" / "a.jsonl"))
    assert [linha["tipo_registro"] for linha in linhas] == ["execucao"]


def test_jsonl_antes_de_iniciar_e_recusado(tmp_path):
    with pytest.raises(RuntimeError, match="iniciar_execucao"):
        Auditoria().gravar_jsonl(tmp_path / "a.jsonl")
    assert not (tmp_path / "a.jsonl").exists()


def test_jsonl_nao_deixa_arquivo_temporario(tmp_path):
    a = _auditoria()
    a.gravar_jsonl(tmp_path / "a.jsonl")
    a.gravar_jsonl(tmp_path / "a.jsonl")  # sobrescreve sem sobra
    assert [p.name for p in tmp_path.iterdir()] == ["a.jsonl"]


def test_repr_nao_expoe_conteudo():
    a = _auditoria()
    a.registrar("ferramentas", "ferramenta", motivo="segredo do motivo")
    assert "segredo" not in repr(a)
    assert str(a.id_execucao) in repr(a)


# --- Versão do código ------------------------------------------------------------------------


def test_versao_vem_da_variavel_no_docker(monkeypatch):
    monkeypatch.setenv("SRAG_VERSAO_CODIGO", "imagem-1a2b3c")
    assert versao.versao_codigo() == "imagem-1a2b3c"


def test_versao_marca_alteracao_nao_commitada(monkeypatch):
    monkeypatch.delenv("SRAG_VERSAO_CODIGO", raising=False)
    respostas = {"rev-parse": "1a2b3c4", "status": " M src/x.py"}
    monkeypatch.setattr(versao, "_git", lambda *arg: respostas[arg[0]])
    assert versao.versao_codigo() == "1a2b3c4-modificado"
    respostas["status"] = ""
    assert versao.versao_codigo() == "1a2b3c4"


@pytest.mark.parametrize("erro", [OSError("sem git"), subprocess.TimeoutExpired("git", 5)])
def test_versao_sem_git_e_desconhecida(monkeypatch, erro):
    monkeypatch.delenv("SRAG_VERSAO_CODIGO", raising=False)

    def falha(*_):
        raise erro

    monkeypatch.setattr(versao, "_git", falha)
    assert versao.versao_codigo() == "desconhecida"


def test_versao_sem_status_ainda_guarda_o_commit(monkeypatch):
    # `git status` lento (disco de rede, WSL) não pode apagar o commit que já se sabe.
    monkeypatch.delenv("SRAG_VERSAO_CODIGO", raising=False)

    def git(*arg):
        if arg[0] == "status":
            raise subprocess.TimeoutExpired("git", 5)
        return "1a2b3c4"

    monkeypatch.setattr(versao, "_git", git)
    assert versao.versao_codigo() == "1a2b3c4-estado-desconhecido"


def test_versao_real_do_repositorio(monkeypatch):
    monkeypatch.delenv("SRAG_VERSAO_CODIGO", raising=False)
    assert versao.versao_codigo() != ""


def test_resultado_truncado_preserva_os_campos_curtos_de_primeiro_nivel():
    # o evento do orquestrador de 18/10: o `modelo` sumia dentro do resumo truncado
    planejadas = [{"ferramenta": "buscar_noticias", "motivo": "m" * 90}] * 30
    evento = _auditoria().registrar("orquestrador", "llm", resultado={
        "modelo": "openai/gpt-4.1-mini", "ferramentas_planejadas": planejadas,
        "chamadas_invalidas": [], "texto": "x" * 5_000, "tentativa": 1, "ok": True,
        "resumo_truncado": "forjado",
    })  # fmt: skip
    resultado = evento["resultado"]
    assert resultado["modelo"] == "openai/gpt-4.1-mini"
    assert resultado["tentativa"] == 1 and resultado["ok"] is True
    assert "texto" not in resultado and "ferramentas_planejadas" not in resultado  # longos
    assert len(resultado["resumo_truncado"]) == LIMITE_RESULTADO  # o de verdade, não o forjado
    assert resultado["tamanho_original"] > LIMITE_RESULTADO


def test_campo_curto_preservado_tambem_e_mascarado():
    evento = _auditoria().registrar("n", "llm", resultado={
        "modelo": "CPF 123.456.789-09", "texto": "x" * 5_000})  # fmt: skip
    assert "123.456" not in evento["resultado"]["modelo"]
