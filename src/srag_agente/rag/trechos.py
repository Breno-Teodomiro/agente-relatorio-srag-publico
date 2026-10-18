"""Divide o dicionário (uma linha de tabela por campo) em um trecho por campo."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Trecho:
    campo: str
    pagina: int
    texto: str


# (página, [nome do campo, tipo, categoria, descrição, características, DBF])
Linha = tuple[int, list[str]]

_COLUNAS = 6
_OU = re.compile(r"\s+ou\s+", flags=re.IGNORECASE)


def _juntar_continuacoes(linhas: list[Linha]) -> list[Linha]:
    """Linha sem DBF continua a anterior: é a mesma linha da tabela cortada pela quebra de página
    (no PDF, o '2-Feminino' do sexo cai na página seguinte). O cabeçalho repetido é descartado.
    """
    juntas: list[Linha] = []
    for pagina, brutas in linhas:
        celulas = [" ".join(c.split()) for c in brutas]
        if len(celulas) != _COLUNAS or celulas[-1] == "DBF":
            continue
        if celulas[-1]:
            juntas.append((pagina, celulas))
        elif juntas:
            anterior_pagina, anterior = juntas[-1]
            unidas = [f"{a} {b}".strip() for a, b in zip(anterior, celulas, strict=True)]
            juntas[-1] = (anterior_pagina, unidas)
    return juntas


def _nomes_de_campo(dbf: str, campos_conhecidos: set[str]) -> list[str]:
    """A célula DBF sai do PDF com o sublinhado deslocado ('CS SEXO _'), às vezes composta
    ('PAC COCBO ou _ PAC DSCBO _'): compara sem espaço nem sublinhado com os nomes da raw.
    """
    sem_sublinhado = {c.replace("_", ""): c for c in campos_conhecidos}
    partes = (re.sub(r"[\s_]", "", p) for p in _OU.split(dbf))
    return [sem_sublinhado[p] for p in partes if p in sem_sublinhado]


def _texto(campo: str, celulas: list[str], maximo_caracteres: int) -> str:
    """Nome e rótulo no início: o modelo de embedding lê só os primeiros ~128 tokens.

    O formato é o que foi medido em `make avaliar-rag` (ADR 0006); mudá-lo pede nova medição.
    """
    nome, tipo, categoria, descricao, caracteristicas, _dbf = celulas
    texto = (
        f"Campo {campo}: {nome}. Tipo: {tipo}. Categorias: {categoria}. "
        f"Descrição: {descricao}. {caracteristicas}"
    )
    return texto[:maximo_caracteres]


def dividir_em_trechos(
    linhas: list[Linha], campos_conhecidos: set[str], maximo_caracteres: int = 1500
) -> list[Trecho]:
    """Um trecho por campo da raw, com a página onde a linha do campo começa.

    Campo do dicionário que não existe na raw (CPF, CNS, nome, endereço: a base pública não os
    traz) não vira trecho. Linha composta ('ID_MN_RESI ou CO_MUN_RES') vira um trecho por nome,
    com o mesmo texto.
    """
    return [
        Trecho(campo, pagina, _texto(campo, celulas, maximo_caracteres))
        for pagina, celulas in _juntar_continuacoes(linhas)
        for campo in _nomes_de_campo(celulas[-1], campos_conhecidos)
    ]
