"""Supressão de células pequenas: contagens de 1 a 4 não são exibidas (risco de reidentificação)."""

LIMIAR_SUPRESSAO = 5
ROTULO_SUPRIMIDO = "<5"
ROTULO_OCULTO = "oculto"


def suprimir(contagem: int) -> int | str:
    return ROTULO_SUPRIMIDO if 0 < contagem < LIMIAR_SUPRESSAO else contagem


def precisa_suprimir(*contagens: int) -> bool:
    return any(0 < c < LIMIAR_SUPRESSAO for c in contagens)


def _algum_suprimido_determinado(contagens: list[int | None], saida: list) -> bool:
    """Um "<5" fica com um só valor possível, para quem conhece a regra e o total exato?

    Quem lê sabe: "<5" vale de 1 a 4; "oculto" vale de 5 até o teto que a regra impõe (ela
    esconde as menores na ordem (valor, posição), então o oculto vem antes de toda exibida com 5
    ou mais: no máximo igual a uma exibida que está depois dele, e menor que uma que está antes);
    a soma das escondidas é total - exibidas.
    """
    escondidas = [i for i, s in enumerate(saida) if isinstance(s, str)]
    soma = sum(contagens[i] for i in escondidas)
    grandes = [(i, s) for i, s in enumerate(saida) if isinstance(s, int) and s >= LIMIAR_SUPRESSAO]

    def teto(j: int) -> int:
        return min((v if j < i else v - 1 for i, v in grandes), default=soma)

    caixas = [
        (1, LIMIAR_SUPRESSAO - 1) if saida[j] == ROTULO_SUPRIMIDO else (LIMIAR_SUPRESSAO, teto(j))
        for j in escondidas
    ]
    minimo = sum(a for a, _ in caixas)
    maximo = sum(b for _, b in caixas)
    return any(
        saida[i] == ROTULO_SUPRIMIDO and max(a, soma - (maximo - b)) == min(b, soma - (minimo - a))
        for i, (a, b) in zip(escondidas, caixas, strict=True)
    )


def suprimir_celulas(contagens: list[int | None]) -> list[int | str | None]:
    """Supressão primária (1 a 4 → "<5") e complementar dentro de uma mesma resposta.

    O total da resposta é exato; com uma única célula "<5", total - exibidas a revelaria. Por
    isso, havendo alguma célula "<5", a menor célula exibida com 5 ou mais casos também sai, como
    "oculto" (no empate, a primeira na ordem recebida). Se ainda assim algum "<5" ficar com um só
    valor possível por intervalo (ex.: [1, 5, 9] -> "<5" + oculto = 6 só fecha com 1 + 5), sai a
    próxima menor, até não haver mais "<5" determinado ou acabarem as células com 5 ou mais.
    Zero não serve de complemento: escondido junto de um "<5", a soma oculta < 5 obrigaria o zero
    a ser zero. `None` (fora da base) passa.
    """
    saida: list[int | str | None] = [None if c is None else suprimir(c) for c in contagens]
    if ROTULO_SUPRIMIDO not in saida:
        return saida
    candidatas = sorted(
        (i for i, c in enumerate(contagens) if c is not None and c >= LIMIAR_SUPRESSAO),
        key=lambda i: (contagens[i], i),
    )
    for i in candidatas:
        saida[i] = ROTULO_OCULTO
        if not _algum_suprimido_determinado(contagens, saida):
            break
    return saida
