import random

from srag_agente.metricas.supressao import (
    ROTULO_OCULTO,
    precisa_suprimir,
    suprimir,
    suprimir_celulas,
    suprimir_com_janelas,
)


def test_contagem_pequena_vira_menor_que_cinco():
    assert suprimir(3) == "<5"
    assert suprimir(1) == "<5"


def test_zero_e_contagens_grandes_passam():
    assert suprimir(0) == 0
    assert suprimir(5) == 5


def test_precisa_suprimir_se_qualquer_contagem_for_pequena():
    assert precisa_suprimir(100, 4)
    assert not precisa_suprimir(100, 5, 0)


# Supressão complementar: com uma única célula "<5" e o total exato, total - exibidas devolveria
# o valor escondido. Por isso a menor célula exibida (>= 5) também some, como "oculto".


def test_suprimir_celulas_sem_pequenas_nao_mexe():
    assert suprimir_celulas([10, 20, 0]) == [10, 20, 0]


def test_suprimir_celulas_lista_vazia():
    assert suprimir_celulas([]) == []


def test_suprimir_celulas_todas_pequenas():
    assert suprimir_celulas([1, 2, 3]) == ["<5", "<5", "<5"]


def test_suprimir_celulas_oculta_a_unica_exibida():
    assert suprimir_celulas([3, 50]) == ["<5", ROTULO_OCULTO]


def test_suprimir_celulas_oculta_a_menor_exibida_e_mantem_as_outras():
    assert suprimir_celulas([60, 20, 3]) == [60, ROTULO_OCULTO, "<5"]


def test_suprimir_celulas_com_duas_pequenas_tambem_oculta_uma():
    assert suprimir_celulas([2, 3, 6, 40]) == ["<5", "<5", ROTULO_OCULTO, 40]


def test_suprimir_celulas_zero_nao_serve_de_complemento():
    # Um zero oculto não protege nada: soma escondida < 5 obriga o oculto a ser 0.
    assert suprimir_celulas([3, 0, 50]) == ["<5", 0, ROTULO_OCULTO]
    assert suprimir_celulas([3, 0]) == ["<5", 0]


def test_suprimir_celulas_empate_oculta_a_primeira_na_ordem():
    assert suprimir_celulas([3, 7, 9, 7]) == ["<5", ROTULO_OCULTO, 9, 7]


def test_suprimir_celulas_preserva_none_e_nao_o_conta():
    assert suprimir_celulas([None, 3, 8, 20]) == [None, "<5", ROTULO_OCULTO, 20]
    assert suprimir_celulas([None, None]) == [None, None]
    assert suprimir_celulas([None, 4]) == [None, "<5"]


def test_suprimir_celulas_nunca_deixa_uma_so_escondida():
    """Com total exato, uma única célula escondida sairia direto de total - exibidas."""
    for contagens in ([60, 20, 3], [3, 50], [4, 5, 5], [1, 9, 30, 0]):
        saida = suprimir_celulas(contagens)
        escondidas = [c for c, s in zip(contagens, saida, strict=True) if isinstance(s, str)]
        assert len(escondidas) != 1


# Raciocínio por intervalo: "<5" vale de 1 a 4; "oculto" vale de 5 até a menor célula exibida com
# 5 ou mais (a regra é pública: ela esconde as menores). Com a soma das escondidas conhecida
# (total exato - exibidas), um extremo pode fixar o valor de um "<5". Esses casos pedem mais
# complemento.


def _suprimidas_determinadas(contagens, saida) -> int:
    escondidas = [i for i, s in enumerate(saida) if isinstance(s, str)]
    soma = sum(contagens[i] for i in escondidas)
    grandes = [(i, s) for i, s in enumerate(saida) if isinstance(s, int) and s >= 5]

    def teto(j):  # o oculto vem antes, na ordem (valor, posição), de toda exibida >= 5
        return min((v if j < i else v - 1 for i, v in grandes), default=soma)

    caixas = [(1, 4) if saida[j] == "<5" else (5, teto(j)) for j in escondidas]
    minimo, maximo = sum(a for a, _ in caixas), sum(b for _, b in caixas)
    return sum(
        1
        for (a, b), j in zip(caixas, escondidas, strict=True)
        if saida[j] == "<5" and max(a, soma - (maximo - b)) == min(b, soma - (minimo - a))
    )


def test_empate_em_cinco_pede_segundo_complemento():
    # Com só o 1º 5 oculto, ele ficaria preso em 5 (o outro 5 exibido é o teto) e o 3 sairia.
    assert suprimir_celulas([3, 5, 5, 20]) == ["<5", ROTULO_OCULTO, ROTULO_OCULTO, 20]


def test_soma_escondida_seis_pede_segundo_complemento():
    # "<5" + oculto = 6 só fecha com 1 + 5.
    assert suprimir_celulas([1, 5, 9]) == ["<5", ROTULO_OCULTO, ROTULO_OCULTO]


def test_oculto_preso_no_teto_pede_segundo_complemento():
    # Oculto <= 9 (o outro 9 exibido) e soma 13 obrigariam o "<5" a ser 4.
    assert suprimir_celulas([4, 9, 9, 30]) == ["<5", ROTULO_OCULTO, ROTULO_OCULTO, 30]


def test_empate_pela_ordem_aperta_o_teto_do_oculto():
    # O 5 (posição 3) só sai antes do 6 exibido (posição 2) se for menor que 6: fica preso em 5.
    assert suprimir_celulas([22, 3, 6, 5]) == [22, "<5", ROTULO_OCULTO, ROTULO_OCULTO]
    # O 8 oculto vem depois do 9 exibido: vale no máximo 8, e soma 12 fixaria o "<5" em 4.
    assert suprimir_celulas([4, 9, 8]) == ["<5", ROTULO_OCULTO, ROTULO_OCULTO]


def test_um_complemento_basta_quando_o_intervalo_fica_aberto():
    assert suprimir_celulas([4, 7, 9]) == ["<5", ROTULO_OCULTO, 9]
    assert suprimir_celulas([2, 6, 8]) == ["<5", ROTULO_OCULTO, 8]


def test_propriedade_nenhuma_suprimida_fica_determinada_por_intervalo():
    rnd = random.Random(20261016)
    exaustas = 0
    for _ in range(5000):
        contagens = [
            rnd.choice(
                [
                    None,
                    0,
                    rnd.randint(1, 4),
                    rnd.randint(1, 4),
                    rnd.randint(5, 12),
                    rnd.randint(5, 60),
                ]
            )
            for _ in range(rnd.randint(1, 10))
        ]
        saida = suprimir_celulas(contagens)
        assert [s is None for s in saida] == [c is None for c in contagens]
        escondidas = [s for s in saida if isinstance(s, str)]
        assert len(escondidas) != 1 or (
            escondidas == ["<5"] and not any(c is not None and c >= 5 for c in contagens)
        )
        if not any(isinstance(s, int) and s >= 5 for s in saida) and _suprimidas_determinadas(
            contagens, saida
        ):
            # Sem célula >= 5 sobrando não há mais o que esconder (só o total, que é exato).
            exaustas += 1
            continue
        assert _suprimidas_determinadas(contagens, saida) == 0, contagens
    assert exaustas < 5000


# Sub-janelas com soma publicada (semana do aumento, dias em consolidação, mês): o total não é a
# única soma exata. Com a soma de uma sub-janela, soma - exibidas dentro dela devolveria uma
# célula escondida sozinha ali, mesmo que o "oculto" global esteja fora. A supressão complementar
# precisa fechar dentro de cada bloco (conjunto de posições que estão nas mesmas janelas).


def _blocos(n, janelas):
    """Implementação independente da do módulo: agrupa por assinatura de pertença às janelas."""
    grupos = {}
    for i in range(n):
        grupos.setdefault(tuple(i in j for j in janelas), []).append(i)
    return list(grupos.values())


def _determinadas_por_bloco(contagens, saida, janelas):
    """Quantos "<5" ficam com um só valor possível para quem conhece a soma de cada bloco.

    Saber a soma de cada bloco é mais do que se publica (só as somas das janelas): proteger
    contra esse leitor protege contra o leitor real. Devolve também se algum bloco esgotou
    (sobrou "<5" determinado sem nenhuma célula >= 5 exibida para esconder).
    """
    determinadas, esgotou = 0, False
    for bloco in _blocos(len(contagens), janelas):
        sub_c = [contagens[i] for i in bloco]
        sub_s = [saida[i] for i in bloco]
        n = _suprimidas_determinadas(sub_c, sub_s)
        if n and not any(isinstance(s, int) and s >= 5 for s in sub_s):
            esgotou = True
            continue
        determinadas += n
    return determinadas, esgotou


def test_sem_janelas_e_igual_a_supressao_da_resposta_inteira():
    for contagens in ([60, 20, 3], [3, 50], [None, 3, 8, 20], [], [10, 20, 0]):
        assert suprimir_com_janelas(contagens, []) == suprimir_celulas(contagens)


def test_complemento_fica_dentro_da_janela_do_suprimido():
    # Sem janela, o oculto seria o 1º 10 (posição 0); com a soma da janela {2, 3} publicada,
    # soma - 10 devolveria o 3. O complemento tem de estar na mesma janela.
    assert suprimir_celulas([10, 10, 3, 10]) == [ROTULO_OCULTO, 10, "<5", 10]
    assert suprimir_com_janelas([10, 10, 3, 10], [{2, 3}]) == [10, 10, "<5", ROTULO_OCULTO]


def test_janela_sem_suprimido_nao_perde_celula():
    assert suprimir_com_janelas([20, 20, 20, 20], [{0, 1}]) == [20, 20, 20, 20]


def test_janelas_que_se_cruzam_formam_blocos_menores():
    # {0..3} e {2..5}: blocos {0,1}, {2,3}, {4,5}. O 3 está em {2,3}: complemento ali.
    saida = suprimir_com_janelas([9, 9, 3, 8, 7, 7], [{0, 1, 2, 3}, {2, 3, 4, 5}])
    assert saida == [9, 9, "<5", ROTULO_OCULTO, 7, 7]


def test_bloco_sem_celula_grande_nao_pede_complemento_de_fora():
    # Esconder célula de outro bloco não protege nada: a soma do bloco já é conhecida.
    assert suprimir_com_janelas([2, 3, 50, 60], [{0, 1}]) == ["<5", "<5", 50, 60]


def test_propriedade_nenhum_bloco_deixa_suprimido_determinado():
    rnd = random.Random(20261017)
    esgotados = 0
    for _ in range(5000):
        n = rnd.randint(1, 30)
        contagens = [
            rnd.choice([0, rnd.randint(1, 4), rnd.randint(5, 12), rnd.randint(5, 60)])
            for _ in range(n)
        ]
        janelas = []
        for _ in range(rnd.randint(0, 4)):
            inicio = rnd.randrange(n)
            janelas.append(set(range(inicio, rnd.randint(inicio, n - 1) + 1)))
        saida = suprimir_com_janelas(contagens, janelas)
        assert [suprimir(c) == s for c, s in zip(contagens, saida, strict=True) if c < 5] == [
            True for c in contagens if c < 5
        ]
        determinadas, esgotou = _determinadas_por_bloco(contagens, saida, janelas)
        esgotados += esgotou
        assert determinadas == 0, (contagens, janelas)
    assert esgotados < 5000
