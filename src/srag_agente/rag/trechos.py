"""Divide o texto do dicionário (uma tabela por campo) em um trecho por campo."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Trecho:
    campo: str | None
    pagina: int
    texto: str


def dividir_em_trechos(
    paginas: list[str],
    campos_conhecidos: set[str],
    minimo_caracteres: int = 40,
    maximo_caracteres: int = 1500,
) -> list[Trecho]:
    """No PDF extraído, o nome do campo aparece DEPOIS da sua descrição: ele fecha o trecho."""
    trechos: list[Trecho] = []
    buffer: list[str] = []
    pagina_atual = 1
    for pagina_atual, texto in enumerate(paginas, start=1):
        for linha in (bruta.strip() for bruta in texto.splitlines()):
            if not linha:
                continue
            acumulado = " ".join(buffer)
            if linha in campos_conhecidos and len(acumulado) >= minimo_caracteres:
                trechos.append(
                    Trecho(linha, pagina_atual, f"Campo {linha}: {acumulado[-maximo_caracteres:]}")
                )
                buffer = []
            else:
                buffer.append(linha)
    if buffer:
        trechos.append(Trecho(None, pagina_atual, " ".join(buffer)[-maximo_caracteres:]))
    return trechos
