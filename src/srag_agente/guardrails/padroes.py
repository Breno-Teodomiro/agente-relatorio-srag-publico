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
_DADOS_PESSOAIS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "cartao_sus": re.compile(r"(?<!\d)[1-9]\d{2}[ .-]?\d{4}[ .-]?\d{4}[ .-]?\d{4}(?!\d)"),
    "pis": re.compile(r"(?<!\d)\d{3}\.\d{5}\.\d{2}-\d(?!\d)"),
    "cpf": re.compile(r"(?<!\d)\d{3}[ .]?\d{3}[ .]?\d{3}[ ./-]?\d{2}(?!\d)"),
    "rg": re.compile(r"(?<![\d.])\d{1,2}\.\d{3}\.\d{3}-[\dXx](?!\w)"),
    "telefone": re.compile(
        r"(?:\+?55[ .-]?)?\(\d{2}\)[ .-]?9?\d{4}[ .-]?\d{4}(?!\d)"  # (11) 98765-4321 e fixo
        r"|(?:\+?55[ .-]?)?(?<!\d)\d{2}[ .-]?9\d{4}[ .-]?\d{4}(?!\d)"  # 11 98765-4321
        r"|(?<!\d)9\d{4}-\d{4}(?!\d)"  # 98765-4321
    ),
}
# Identificadores que a auditoria grava e que têm trechos só de dígitos: UUID canônico e hash
# hexadecimal (git, sha256). Ficam de fora do mascaramento, senão "…-a716-446655440000" vira
# cartão SUS.
_IDENTIFICADOR = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b(?=[0-9a-f]*[a-f])[0-9a-f]{32,}\b",
    re.IGNORECASE,
)
MASCARA = "[dado removido]"


def _sem_invisiveis(texto: str) -> str:
    return "".join(c for c in texto if unicodedata.category(c) != "Cf")


def _trocar_trecho(texto: str, encontrados: list[str]) -> str:
    for tipo, padrao in _DADOS_PESSOAIS.items():
        texto, quantidade = padrao.subn(MASCARA, texto)
        encontrados += [tipo] * quantidade
    return texto


def _trocar(texto: str) -> tuple[str, list[str]]:
    encontrados: list[str] = []
    pedacos, inicio = [], 0
    for achado in _IDENTIFICADOR.finditer(texto):
        pedacos += [_trocar_trecho(texto[inicio : achado.start()], encontrados), achado.group(0)]
        inicio = achado.end()
    pedacos.append(_trocar_trecho(texto[inicio:], encontrados))
    return "".join(pedacos), encontrados


def mascarar_pii(texto: str) -> tuple[str, list[str]]:
    """Troca CPF, cartão SUS, PIS, RG, telefone e e-mail por ``[dado removido]``.

    Devolve o texto e os tipos encontrados (um por ocorrência). Os caracteres invisíveis de
    formato (``Cf``: largura zero, inversão bidirecional) saem SEMPRE, porque escondem o
    documento do padrão e não têm uso legítimo num relatório. A forma NFKC (que desfaz ponto e
    hífen de largura total) só substitui o texto quando esconde um dado pessoal: sem isso, nada
    visível muda ("1º" continua "1º").

    Limitações: documento soletrado ("um dois três…"), com um dígito por linha ou com separador
    fora do padrão (``123_456``) passa; RG de formato estadual sem dígito verificador também.
    """
    mascarado, encontrados = _trocar(_sem_invisiveis(texto or ""))
    normalizado = unicodedata.normalize("NFKC", mascarado)
    if normalizado != mascarado:
        renormalizado, extras = _trocar(normalizado)
        if extras:
            mascarado, encontrados = renormalizado, encontrados + extras
    return mascarado, encontrados
