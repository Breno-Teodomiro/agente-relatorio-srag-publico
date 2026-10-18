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
        # formatos que escapavam (revisão da Task 11, I1)
        ("cpf 123-456-789-09", "cpf"),  # hífen em todos os separadores
        ("cpf 123,456,789-09", "cpf"),  # vírgula no lugar do ponto
        ("cpf 123.456.789\u201009", "cpf"),  # hífen tipográfico U+2010
        ("cpf 123.456.789\u201109", "cpf"),  # hífen inseparável U+2011
        ("cpf 123.456.789\u201209", "cpf"),  # figure dash U+2012
        ("cpf 123.456.789\u201309", "cpf"),  # meia-risca U+2013
        ("cpf 123.456.789\u201409", "cpf"),  # travessão U+2014
        ("cpf 123.456.789\u201509", "cpf"),  # barra horizontal U+2015
        ("cpf 123.456.789\u221209", "cpf"),  # sinal de menos U+2212
        ("cpf 123.456.789\ufe5809", "cpf"),  # travessão pequeno U+FE58
        ("cpf 123.456.789\ufe6309", "cpf"),  # hífen pequeno U+FE63
        ("cpf 123\u2013456\u2013789\u201309", "cpf"),  # meia-risca em todos
        ("ligue (11) 9 8765-4321", "telefone"),  # 9 separado
        ("ligue 11 9 8765-4321", "telefone"),  # 9 separado, sem parênteses
        ("ligue 9 8765-4321", "telefone"),  # sem DDD, 9 separado
        ("ligue +5511987654321", "telefone"),  # +55 sem separador
        ("ligue 5511987654321", "telefone"),  # 55 sem o +
        ("ligue +551134567890", "telefone"),  # fixo com +55 sem separador
        ("ligue 11 3456-7890", "telefone"),  # fixo com DDD sem parênteses
        ("ligue 11-3456-7890", "telefone"),  # fixo com DDD e hífen
        ("ligue (11) 98765\u20134321", "telefone"),  # meia-risca no telefone
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
        # M8: sequência de números separados por espaço não é CPF (dígito verificador inválido)
        "valores 100 200 300 400 50",
        "séries 111 222 333 44 e 555 666 777 88",
        "Semanas 2024\u20132025, de 25/06/2025 a 2025\u201306\u201311.",  # meia-risca sem PII
        "Foram 12 345 678 casos em 2025.",
    ],
)
def test_numeros_legitimos_nao_sao_mascarados(texto):
    assert mascarar_pii(texto) == (texto, [])


@pytest.mark.parametrize(
    "texto",
    [
        "cpf 123 456 789 09",  # só espaços: vale porque o dígito verificador confere
        "cpf 12345678909",  # 11 dígitos colados: idem
        "cpf 123.456 789-09",  # separadores misturados: idem
    ],
)
def test_cpf_sem_formato_canonico_so_com_digito_verificador(texto):
    assert mascarar_pii(texto)[1] == ["cpf"]
    # o mesmo formato com dígito verificador errado é número qualquer
    errado = texto[:-1] + ("0" if texto[-1] != "0" else "1")
    assert mascarar_pii(errado)[1] == []


def test_cpf_formatado_e_mascarado_mesmo_com_digito_verificador_errado():
    # pontuação de CPF é sinal forte: um CPF digitado com erro continua sendo dado pessoal
    assert mascarar_pii("cpf 123.456.789-00")[1] == ["cpf"]


def test_candidato_invalido_nao_esconde_cpf_valido_logo_depois():
    # "100 200 300 40" é candidato (inválido); o CPF válido depois dele ainda tem de sair
    mascarado, tipos = mascarar_pii("lista 100 200 300 40 e cpf 123 456 789 09")
    assert tipos == ["cpf"] and "789" not in mascarado and "100 200 300 40" in mascarado


def test_texto_sem_dado_pessoal_so_perde_caracteres_invisiveis():
    # sem PII, nada visível muda (nem "1º" vira "1o"); os invisíveis (Cf) saem sempre
    assert mascarar_pii("1º boletim\u200b, m² e ½") == ("1º boletim, m² e ½", [])


def test_inversao_bidirecional_e_removida():
    mascarado, _ = mascarar_pii("texto \u202eoculto\u202c normal")
    assert "\u202e" not in mascarado and "\u202c" not in mascarado


@pytest.mark.parametrize("vazio", ["", None])
def test_vazio(vazio):
    assert mascarar_pii(vazio) == ("", [])


# --- Identificadores (UUID, hash) não podem esconder um documento colado neles ----------------
# O hash hexadecimal fica fora do mascaramento (senão "…446655440000" viraria cartão SUS). Mas um
# documento COLADO num texto hexadecimal longo não pode sumir junto com ele.
HEX = "b" * 50


@pytest.mark.parametrize(
    "texto,tipo",
    [
        (HEX + "123.456.789-09", "cpf"),  # o caso que vazava: devolvia []
        ("123.456.789-09" + HEX, "cpf"),
        (HEX + "12345678909", "cpf"),  # sem pontuação, dígito verificador confere
        # o pedaço hexadecimal tem tamanho exato de hash (29 + "123" = 32; 40): o documento
        # atravessa a borda do "identificador" e sai mesmo assim
        ("b" * 29 + "123.456.789-09", "cpf"),
        ("a" * 40 + "@exemplo.com", "email"),
        (HEX + "fulano@exemplo.com", "email"),
        ("dead" * 10 + "beef@exemplo.com.br", "email"),
        (HEX + "11 98765-4321", "telefone"),
        (HEX + "(11) 98765-4321", "telefone"),
    ],
)
def test_documento_colado_em_texto_hexadecimal_longo_e_mascarado(texto, tipo):
    mascarado, tipos = mascarar_pii(texto)
    assert tipos == [tipo]
    assert MASCARA in mascarado
    assert "98765" not in mascarado and "456" not in mascarado and "@" not in mascarado


@pytest.mark.parametrize(
    "identificador",
    [
        "3f786850e387550fdab836ed7e6dc881de23001b",  # sha1 (git)
        "d41d8cd98f00b204e9800998ecf8427e",  # md5
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",  # sha256
        "a1234567890123456789012345678901",  # 32 hex com 31 dígitos seguidos: ainda é hash
        "550e8400-e29b-41d4-a716-446655440000",  # UUID
    ],
)
@pytest.mark.parametrize("molde", ["{}", "commit {} ok", '{{"id": "{}"}}', "({})", "{}, {}"])
def test_hash_e_uuid_legitimos_continuam_intactos(identificador, molde):
    texto = molde.format(identificador, identificador)
    assert mascarar_pii(texto) == (texto, [])


# --- Documento colado em dígitos: nunca sobra pedaço numérico ao lado da máscara (revisão Task 15)
# Antes: "123456789012123.456.789-09" virava "[dado removido].456.789-09" (8 dos 11 dígitos do
# CPF, verificadores inclusive) e "1234123.456.789-09" passava inteiro.
@pytest.mark.parametrize(
    "documento",
    [
        "123456789012123.456.789-09",  # o caso da revisão: 15 dígitos "de SUS" + resto do CPF
        "1234123.456.789-09",  # CPF formatado com dígitos colados à esquerda
        "898001234567890123.456.789-09",  # cartão SUS colado num CPF formatado
        "123.456.789-09.987",  # CPF formatado seguido de ".dígitos"
        "123.456.789-09/12",
        "12345678909.5",  # CPF sem pontuação seguido de ".dígito"
        "7-12345678909",  # e precedido de "dígito-"
        "898001160564312.12",  # cartão SUS seguido de ".dígitos"
        "12-898001160564312",  # e precedido de "dígitos-"
        "(11) 98765-4321.55",  # telefone seguido de ".dígitos"
        "98765-4321-12",  # telefone sem DDD seguido de "-dígitos"
        "3-11 98765-4321",  # telefone com DDD precedido de "dígito-"
    ],
)
def test_documento_colado_em_digitos_sai_inteiro(documento):
    mascarado, tipos = mascarar_pii(f"paciente {documento} internado")
    assert mascarado == f"paciente {MASCARA} internado"
    assert tipos  # foi reconhecido como dado pessoal


def test_extensao_nao_atravessa_espaco_nem_pontuacao_final():
    # só a sequência ENCOSTADA (dígitos e . , / -) sai junto; o resto do texto fica
    texto = "Em 2025, 123.456.789-09. Foram 53.476 casos."
    assert mascarar_pii(texto) == (f"Em 2025, {MASCARA}. Foram 53.476 casos.", ["cpf"])


@pytest.mark.parametrize(
    "identificador",
    [  # os dois terminam e começam em dígito, encostados no documento só pelo "-"
        "550e8400-e29b-41d4-a716-446655440000",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    ],
)
def test_extensao_nao_come_identificador_vizinho(identificador):
    # o documento encostado sai; o UUID/hash ao lado (que não é dado pessoal) fica inteiro
    cpf = "123.456.789-09"
    assert mascarar_pii(f"{identificador}-{cpf}") == (f"{identificador}-{MASCARA}", ["cpf"])
    assert mascarar_pii(f"{cpf}-{identificador}") == (f"{MASCARA}-{identificador}", ["cpf"])


@pytest.mark.parametrize(
    "texto",
    [
        "Custo de R$ 1.234,56 e variação de -12,5% (45,3% do total).",
        "Data de referência 12/03/2026; execução em 2025-10-17.",
        "Total de 165.397 casos e 1.234.567.890 doses aplicadas.",
        "De 10/2024 a 09/2025, IC 95%: 1,2-3,4; versão 1.2.3.",
        "Semana 2025-42: 12.345 internações, 3.210 em UTI (26,0%).",
        "sha256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 em 2025",
        "execução 550e8400-e29b-41d4-a716-446655440000 de 17/10/2026",
    ],
)
def test_texto_comum_com_numeros_continua_intacto(texto):
    assert mascarar_pii(texto) == (texto, [])


@pytest.mark.xfail(strict=True, reason="limitação declarada: documento dentro de falso hash")
def test_documento_escondido_em_falso_hash_de_tamanho_exato():
    # "b"*21 + CPF válido = 32 caracteres hexadecimais com letra: tem tamanho de md5 e é poupado.
    # Distinguir isso de um md5 de verdade exigiria adivinhar; fica declarado em mascarar_pii.
    assert MASCARA in mascarar_pii("b" * 21 + "12345678909")[0]
