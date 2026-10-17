"""Mascaramento de dados pessoais: formatos reais, ofuscação Unicode e números legítimos.

Todos os documentos abaixo são fictícios (sequências inventadas, sem dono).
"""

import re

import pytest

from srag_agente.guardrails.padroes import MASCARA, mascarar_pii
from tests.guardrails.ofuscacao import largura_total


@pytest.mark.parametrize(
    "texto,tipo",
    [
        ("CPF 123.456.789-09 do paciente", "cpf"),
        ("cpf 12345678909", "cpf"),
        ("cpf 123 456 789 09", "cpf"),
        ("cpf 123456789-09", "cpf"),
        ("CPF12345678909", "cpf"),  # colado no rótulo
        ("paciente x123.456.789-09", "cpf"),  # colado em letra qualquer
        ("cartão SUS 898001160564312", "cartao_sus"),
        ("CNS 898 0011 6056 4312", "cartao_sus"),
        ("ligue (11) 98765-4321", "telefone"),
        ("ligue (11) 3456-7890", "telefone"),
        ("ligue 11 98765-4321", "telefone"),
        ("ligue +55 11 98765-4321", "telefone"),
        ("ligue 98765-4321", "telefone"),
        ("escreva para fulano.tal+srag@exemplo.com.br", "email"),
        ("RG 12.345.678-9", "rg"),
        ("PIS 123.45678.90-1", "pis"),
    ],
)
def test_mascara_cada_tipo(texto, tipo):
    mascarado, tipos = mascarar_pii(texto)
    assert MASCARA in mascarado
    assert tipos == [tipo]
    assert not re.search(r"\d{4}", mascarado)  # nenhum pedaço do documento sobrou


def test_devolve_um_tipo_por_ocorrencia():
    _, tipos = mascarar_pii("123.456.789-09 e 987.654.321-00, contato a@b.com")
    assert sorted(tipos) == ["cpf", "cpf", "email"]


@pytest.mark.parametrize(
    "texto",
    [
        largura_total("123.456.789-09", so_digitos=True),  # dígitos de largura total
        "123\uff0e456\uff0e789\uff0d09",  # ponto e hífen de largura total
        "123.456\u200b.789-09",  # espaço de largura zero no meio
        "123.4\u206056.789-09",  # word joiner
    ],
)
def test_ofuscacao_unicode_nao_escapa(texto):
    mascarado, tipos = mascarar_pii(f"paciente {texto} internado")
    assert tipos == ["cpf"]
    assert "789" not in mascarado and "\uff17\uff18\uff19" not in mascarado


@pytest.mark.parametrize(
    "texto",
    [
        "Foram 53.476 casos e 1.500 óbitos em 2025.",
        "A mortalidade foi de 10,0% (80 de 800).",
        "Semanas 2024-2025, de 25/06/2025 a 2025-06-11.",
        "Total de 165.397 casos na silver; 12.345.678 habitantes.",
        "O boletim InfoGripe apontou alta de 15% [1].",
        "H1N1, H3N2 e SARS-CoV-2 circularam; COVID-19 em queda.",
        "Veja https://www.gov.br/saude/n2 e gov.br.",
        "id 550e8400-e29b-41d4-a716-446655440000",
        "commit 3f786850e387550fdab836ed7e6dc881de23001b",  # hash do git na auditoria
    ],
)
def test_numeros_legitimos_nao_sao_mascarados(texto):
    assert mascarar_pii(texto) == (texto, [])


def test_texto_sem_dado_pessoal_so_perde_caracteres_invisiveis():
    # sem PII, nada visível muda (nem "1º" vira "1o"); os invisíveis (Cf) saem sempre
    assert mascarar_pii("1º boletim\u200b, m² e ½") == ("1º boletim, m² e ½", [])


def test_inversao_bidirecional_e_removida():
    mascarado, _ = mascarar_pii("texto \u202eoculto\u202c normal")
    assert "\u202e" not in mascarado and "\u202c" not in mascarado


@pytest.mark.parametrize("vazio", ["", None])
def test_vazio(vazio):
    assert mascarar_pii(vazio) == ("", [])
