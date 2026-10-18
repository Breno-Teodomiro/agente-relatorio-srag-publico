"""Dado pessoal não entra na trilha de auditoria (memória, JSONL e, por consequência, Postgres).

O mascaramento roda em `resumir` (argumentos e resultado) e no corte de motivo/erro, ANTES do
corte por tamanho: um CPF na fronteira do corte não pode sobrar pela metade.
"""

from srag_agente.auditoria.registro import LIMITE_RESULTADO, Auditoria, resumir, sem_pii
from srag_agente.guardrails.padroes import MASCARA

CPF = "123.456.789-09"  # fictício


def _auditoria() -> Auditoria:
    a = Auditoria()
    a.iniciar_execucao(provedor="p", modelo="m", parametros={}, versao_codigo="v",
                       hash_prompts="h")  # fmt: skip
    return a


def test_argumentos_resultado_motivo_e_erro_sao_mascarados(tmp_path):
    a = _auditoria()
    evento = a.registrar(
        "ferramentas",
        "ferramenta",
        argumentos={"pergunta": f"paciente CPF {CPF}", "uf": "SP"},
        motivo="contato (11) 98765-4321 do paciente",
        resultado={"lista": [{"email": "fulano@exemplo.com"}], "total": 84},
        erro=f"ValueError: cpf={CPF}",
    )
    assert evento["argumentos"] == {"pergunta": "paciente CPF [dado removido]", "uf": "SP"}
    assert evento["resultado"] == {"lista": [{"email": "[dado removido]"}], "total": 84}
    assert "98765" not in evento["motivo"] and "[dado removido]" in evento["motivo"]
    assert CPF not in evento["erro"]
    texto = a.gravar_jsonl(tmp_path / "a.jsonl").read_text(encoding="utf-8")
    for pedaco in (CPF, "98765-4321", "fulano@"):
        assert pedaco not in texto


def test_numero_inteiro_com_cara_de_documento_e_mascarado():
    assert resumir({"cpf": 12345678909, "cns": 898001160564312, "total": 165397}) == {
        "cpf": "[dado removido]", "cns": "[dado removido]", "total": 165397,
    }  # fmt: skip


def test_chave_com_dado_pessoal_tambem_e_mascarada():
    # argumentos crus do LLM (caminho de erro) podem ter chaves arbitrárias
    assert resumir({f"cpf {CPF}": 1}) == {"cpf [dado removido]": 1}


def test_cpf_na_fronteira_do_corte_nao_sobra_pela_metade():
    resultado = resumir({"texto": "x" * (LIMITE_RESULTADO - 20) + CPF + "y" * 5_000})
    assert "123.456" not in resultado["resumo_truncado"]
    motivo = _auditoria().registrar("n", "decisao", motivo="m" * (LIMITE_RESULTADO - 5) + CPF)
    assert "123" not in motivo["motivo"]


def test_valores_sem_dado_pessoal_nao_mudam():
    valor = {"valor": 0.322222222222222, "data": "2025-06-25", "uf": "BR", "casos": [10, 20],
             "url": "https://www.gov.br/saude/n2", "rotulo": "<5"}  # fmt: skip
    assert resumir(valor) == valor


# ---- revisão final da branch: parâmetros da execução (M6) e mascaramento único (M4) ----------


def test_parametros_da_execucao_sao_mascarados_nas_tres_formas(tmp_path):
    """Defesa em profundidade: o núcleo já mascara a pergunta, mas o registro não depende dele."""
    a = Auditoria()
    a.iniciar_execucao(
        provedor="p", modelo="m", versao_codigo="v", hash_prompts="h",
        parametros={"uf": "BR", "data_execucao": "2026-10-14",
                    "pergunta_extra": f"meu cpf é {CPF}", f"cpf {CPF}": 12345678909},
    )  # fmt: skip
    assert a.execucao["parametros"] == {
        "uf": "BR", "data_execucao": "2026-10-14", "pergunta_extra": "meu cpf é [dado removido]",
        "cpf [dado removido]": "[dado removido]",
    }  # fmt: skip
    texto = a.gravar_jsonl(tmp_path / "a.jsonl").read_text(encoding="utf-8")
    assert "123.456" not in texto and "12345678909" not in texto


def test_sem_pii_cobre_tupla_e_objeto_desconhecido():
    """A mesma função serve à trilha e à máscara do Langfuse (que recebe objetos do LangChain)."""

    class Mensagem:
        def __str__(self) -> str:
            return f"conteúdo com {CPF}"

    assert sem_pii(("a", 12345678909, 0.5, None, True)) == ["a", MASCARA, 0.5, None, True]
    assert sem_pii(Mensagem()) == f"conteúdo com {MASCARA}"


def test_motivo_que_nao_e_texto_vira_texto_mascarado_e_nao_derruba_o_registro():
    # motivo cru do LLM (caminho de erro da ferramenta) pode chegar como número
    evento = _auditoria().registrar("ferramentas", "ferramenta", motivo=12345678909)
    assert evento["motivo"] == MASCARA
    assert _auditoria().registrar("ferramentas", "ferramenta", motivo=42)["motivo"] == "42"
