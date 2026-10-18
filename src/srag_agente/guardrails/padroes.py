"""Padrões de tentativa de injeção de instruções (prompt injection), em português e inglês.

Antes de procurar, o texto é normalizado (NFKC, que desfaz letras de largura total, e remoção dos
caracteres invisíveis de formato, categoria Unicode ``Cf``: espaço de largura zero, hífen invisível,
inversão bidirecional).

Limitações declaradas (testadas como ``xfail`` em ``tests/guardrails/test_padroes.py``):

- homóglifos de outros alfabetos (o cirílico U+043E no lugar do ``o`` latino) não são mapeados;
- conteúdo codificado (base64, rot13, URL-encoding) não é decodificado;
- é uma lista de padrões, não um classificador: paráfrase nova passa. A defesa não depende só
  dela — o conteúdo externo vai ao LLM delimitado em ``<noticia_externa>`` e com aviso de que é
  dado, e os números do relatório nunca vêm do LLM.

Falso positivo aceito: notícia legítima que use frases como "a partir de agora você deve" ou
"ignore as regras" é descartada. Descartar uma notícia custa pouco; segui-la custaria o relatório.
"""

import re
import unicodedata
from collections.abc import Callable

_VERBO_PT = r"(ignore|ignorem|desconsidere|desconsiderem|esque[çc]a|esque[çc]am)"
_DETERMINANTE_PT = r"(todas|todos|as|os|suas|seus|quaisquer|essas|estas)"
_ALVO_PT = r"(instru[çc][õo]es|regras|ordens|comandos)"
_DETERMINANTE_EN = r"(all|any|the|your|my|of|everything)"
_ALVO_EN = r"(previous|prior|above|earlier|instructions|rules)"

_INJECAO = re.compile(
    rf"\b{_VERBO_PT}\s+({_DETERMINANTE_PT}\s+)*{_ALVO_PT}"
    r"|\besque[çc]a\s+tudo\s+(o\s+)?que\b"
    rf"|\b(ignore|disregard|forget)\s+({_DETERMINANTE_EN}\s+)*{_ALVO_EN}\b"
    r"|\bsystem\s+prompt|\bprompt\s+do\s+sistema"
    r"|\bvoc[êe]\s+agora\s+[ée]\b|\byou\s+are\s+now\b"
    r"|\ba\s+partir\s+de\s+agora,?\s+voc[êe]\s+(deve|vai|ser[áa]|[ée])\b"
    r"|\bnew\s+instructions\s*:|\bnovas\s+instru[çc][õo]es\s*:"
    r"|^\s*(system|assistant|sistema|assistente)\s*:"
    r"|\[/?INST\]|<\|\s*(im_start|im_end|system|assistant|user)\s*\|>"
    r"|\bjailbreak\b|(?-i:\bDAN\b)"
    r"|<\s*/?\s*(system|assistant|user|noticia_externa)\b",
    re.IGNORECASE | re.MULTILINE,
)


def normalizar_unicode(texto: str) -> str:
    """NFKC e remoção dos caracteres invisíveis de formato (categoria ``Cf``)."""
    normalizado = unicodedata.normalize("NFKC", texto or "")
    return "".join(c for c in normalizado if unicodedata.category(c) != "Cf")


def contem_injecao(texto: str) -> bool:
    return bool(_INJECAO.search(normalizar_unicode(texto)))


# --- Dados pessoais (Task 11) ---------------------------------------------------------------------
# A ordem importa: e-mail antes (dígitos dentro do endereço), cartão SUS (15 dígitos) antes do CPF
# (11) e do telefone. A borda é só "não colado em dígito": o documento colado em letra
# ("CPF12345678909", "x123.456.789-09") também sai. `\d` do Python aceita dígitos Unicode
# (inclusive os de largura total).
#
# CPF em dois níveis. Com pontuação de CPF (mesmo separador nos dois primeiros pontos, "-" "." ou
# "/" antes do dígito verificador) sai sempre: um CPF digitado com erro continua sendo dado
# pessoal. Sem ela (só espaços, separadores misturados ou 11 dígitos colados) só sai se o dígito
# verificador conferir: "valores 100 200 300 400 50" é uma lista de números, não um documento.
_CPF_FORMATADO = re.compile(
    r"(?<!\d)\d{3}(?P<s>[.,-])\d{3}(?P=s)\d{3}(?:[./-]|(?P=s))\d{2}(?!\d)"  # 123.456.789-09
    r"|(?<!\d)\d{9}[./-]\d{2}(?!\d)"  # 123456789-09
)
_CPF_SOLTO = re.compile(r"(?<!\d)\d{3}[ .,-]?\d{3}[ .,-]?\d{3}[ .,/-]?\d{2}(?!\d)")
_DDI = r"(?:\+\s?55[ .-]?|55[ .-]?)?"
_TELEFONE = re.compile(
    # (11) 98765-4321, (11) 9 8765-4321 e fixo (11) 3456-7890
    rf"(?<![\d+]){_DDI}\(\d{{2}}\)\s?(?:9[ .-]?)?\d{{4}}[ .-]?\d{{4}}(?!\d)"
    # 11 98765-4321, 11 9 8765-4321 e +5511987654321
    rf"|(?<![\d+]){_DDI}\d{{2}}[ .-]?9[ .-]?\d{{4}}[ .-]?\d{{4}}(?!\d)"
    rf"|(?<![\d+]){_DDI}\d{{2}}[ -][2-5]\d{{3}}[ .-]?\d{{4}}(?!\d)"  # fixo com DDD: 11 3456-7890
    r"|(?<![\d+])\+\s?55\d{10}(?!\d)"  # fixo com +55 colado: +551134567890
    r"|(?<![\d+])9[ .]?\d{4}-\d{4}(?!\d)"  # sem DDD: 98765-4321, 9 8765-4321
)


def _cpf_valido(trecho: str) -> bool:
    """Dígitos verificadores do CPF (módulo 11). Sequência repetida (000…, 111…) não vale."""
    digitos = [int(c) for c in trecho if c.isdigit()]
    if len(digitos) != 11 or len(set(digitos)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(d * (tamanho + 1 - i) for i, d in enumerate(digitos[:tamanho]))
        if digitos[tamanho] != (soma * 10 % 11) % 10:
            return False
    return True


Validador = Callable[[str], bool] | None
# (tipo, padrão, validador): sem validador, todo achado é mascarado.
_DADOS_PESSOAIS: tuple[tuple[str, re.Pattern[str], Validador], ...] = (
    ("email", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), None),
    ("cartao_sus", re.compile(r"(?<!\d)[1-9]\d{2}[ .-]?\d{4}[ .-]?\d{4}[ .-]?\d{4}(?!\d)"), None),
    ("pis", re.compile(r"(?<!\d)\d{3}\.\d{5}\.\d{2}-\d(?!\d)"), None),
    ("cpf", _CPF_FORMATADO, None),
    ("cpf", _CPF_SOLTO, _cpf_valido),
    ("rg", re.compile(r"(?<![\d.])\d{1,2}\.\d{3}\.\d{3}-[\dXx](?!\w)"), None),
    ("telefone", _TELEFONE, None),
)
# Traços que escondem o separador do padrão: hífens e travessões (U+2010 a U+2015), sinal de menos
# (U+2212) e as formas pequenas e de largura total (U+FE58, U+FE63, U+FF0D) viram "-".
TRACOS = dict.fromkeys((*range(0x2010, 0x2016), 0x2212, 0xFE58, 0xFE63, 0xFF0D), "-")
# Identificadores que a auditoria grava e que têm trechos só de dígitos: UUID canônico e hash
# hexadecimal com tamanho de hash de verdade (md5 32, sha1/git 40, sha256 64, sha512 128). Um
# achado que cabe INTEIRO dentro de um deles não é mascarado, senão "…-a716-446655440000" vira
# cartão SUS. Duas regras impedem que o identificador esconda um documento colado nele:
# - tamanho exato: "b"*50 + "12345678909" não é hash (61 caracteres), então o CPF aparece;
# - só o achado inteiro lá dentro é poupado: o que atravessa a borda ("<hash>123.456.789-09",
#   "<hex>beef@exemplo.com") é documento colado e sai.
_IDENTIFICADOR = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b(?=[0-9a-f]*[a-f])(?:[0-9a-f]{128}|[0-9a-f]{64}|[0-9a-f]{40}|[0-9a-f]{32})\b",
    re.IGNORECASE,
)
MASCARA = "[dado removido]"


def _sem_invisiveis(texto: str) -> str:
    return "".join(c for c in texto if unicodedata.category(c) != "Cf")


def _dentro_de_identificador(achado: re.Match[str], identificadores: list[tuple[int, int]]) -> bool:
    return any(ini <= achado.start() and achado.end() <= fim for ini, fim in identificadores)


def _trocar_padrao(
    texto: str, tipo: str, padrao: re.Pattern[str], valido: Validador, encontrados: list[str]
) -> str:
    """Mascara cada achado válido. O inválido (ou inteiro dentro de um identificador) não
    consome o texto: a busca recomeça um caractere adiante, para um candidato falso não esconder
    um documento que começa dentro dele."""
    # Recalculado a cada padrão: a troca do padrão anterior muda as posições do texto.
    identificadores = [a.span() for a in _IDENTIFICADOR.finditer(texto)]
    pedacos, inicio, posicao = [], 0, 0
    while achado := padrao.search(texto, posicao):
        poupar = _dentro_de_identificador(achado, identificadores)
        if not poupar and (valido is None or valido(achado.group(0))):
            pedacos += [texto[inicio : achado.start()], MASCARA]
            inicio = posicao = achado.end()
            encontrados.append(tipo)
        else:
            posicao = achado.start() + 1
    pedacos.append(texto[inicio:])
    return "".join(pedacos)


def _trocar(texto: str) -> tuple[str, list[str]]:
    encontrados: list[str] = []
    for tipo, padrao, valido in _DADOS_PESSOAIS:
        texto = _trocar_padrao(texto, tipo, padrao, valido, encontrados)
    return texto, encontrados


def mascarar_pii(texto: str) -> tuple[str, list[str]]:
    """Troca CPF, cartão SUS, PIS, RG, telefone e e-mail por ``[dado removido]``.

    Devolve o texto e os tipos encontrados (um por ocorrência). Os caracteres invisíveis de
    formato (``Cf``: largura zero, inversão bidirecional) saem SEMPRE, porque escondem o
    documento do padrão e não têm uso legítimo num relatório. A forma normalizada (NFKC, que
    desfaz ponto e hífen de largura total, e os traços de ``TRACOS`` trocados por "-") é
    procurada também, mas só substitui o texto quando esconde um dado pessoal: sem isso, nada
    visível muda ("1º" continua "1º" e a meia-risca de um intervalo de anos fica como está).

    Limitações: documento soletrado ("um dois três…"), com um dígito por linha ou com separador
    fora do padrão (``123_456``) passa; CPF sem pontuação e com dígito verificador errado também
    (não é de ninguém, mas pode ser um CPF real digitado errado); RG de formato estadual sem
    dígito verificador também.
    """
    mascarado, encontrados = _trocar(_sem_invisiveis(texto or ""))
    normalizado = unicodedata.normalize("NFKC", mascarado).translate(TRACOS)
    if normalizado != mascarado:
        renormalizado, extras = _trocar(normalizado)
        if extras:
            mascarado, encontrados = renormalizado, encontrados + extras
    return mascarado, encontrados
