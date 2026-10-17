"""Divide o texto do dicionário (uma tabela por campo) em um trecho por campo."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Trecho:
    campo: str | None
    pagina: int
    texto: str


_CABECALHO_PAGINA = re.compile(r"^SIVEP Gripe-\s*Sistema de Informação.*Página\s+\d+$")
_IDENTIFICADOR = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")
_OU = re.compile(r"\s+ou(?:\s+|$)", flags=re.IGNORECASE)


def _nomes_de_campo(linha: str, campos_conhecidos: set[str]) -> list[str]:
    """Nomes de campo numa linha composta ('ID_MUNICIP OU', 'PAC_COCBO ou'); vazio se não for."""
    partes = [p.strip() for p in _OU.split(linha)]
    if len(partes) < 2:
        return []
    return [p for p in partes if p in campos_conhecidos or _IDENTIFICADOR.match(p)]


def dividir_em_trechos(
    paginas: list[str],
    campos_conhecidos: set[str],
    minimo_caracteres: int = 40,
    maximo_caracteres: int = 1500,
) -> list[Trecho]:
    """No PDF extraído, o nome do campo aparece DEPOIS da sua descrição: ele fecha o trecho.

    Alguns campos vêm em duas linhas ('ID_UN_INTE OU' e depois 'CO_UN_INTE'): a linha composta
    fecha o trecho do primeiro nome e o nome seguinte vira um trecho de mesmo texto.
    """
    trechos: list[Trecho] = []
    buffer: list[str] = []
    alias_de: str | None = None
    pagina_atual = 1

    def fechar(campo: str, pagina: int, acumulado: str) -> None:
        trechos.append(Trecho(campo, pagina, f"Campo {campo}: {acumulado[:maximo_caracteres]}"))

    for pagina_atual, texto in enumerate(paginas, start=1):
        for linha in (bruta.strip() for bruta in texto.splitlines()):
            if not linha or _CABECALHO_PAGINA.match(linha):
                continue
            acumulado = " ".join(buffer)
            if alias_de is not None:
                alias_de = None
                if linha in campos_conhecidos or _IDENTIFICADOR.match(linha):
                    fechar(linha, pagina_atual, trechos[-1].texto.split(": ", 1)[1])
                    continue
            nomes = _nomes_de_campo(linha, campos_conhecidos)
            if nomes and acumulado:
                fechar(nomes[0], pagina_atual, acumulado)
                buffer, alias_de = [], nomes[0]
            elif linha in campos_conhecidos and len(acumulado) >= minimo_caracteres:
                fechar(linha, pagina_atual, acumulado)
                buffer = []
            else:
                buffer.append(linha)
    if buffer:
        trechos.append(Trecho(None, pagina_atual, " ".join(buffer)[:maximo_caracteres]))
    return trechos
