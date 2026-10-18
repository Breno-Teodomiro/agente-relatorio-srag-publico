"""Guardrail de entrada para a pergunta complementar opcional do usuário.

Ordem: regras determinísticas primeiro (custo zero, sem chamar o LLM), classificador LLM só no
fim. Qualquer falha do classificador (erro, resposta fora do formato) RECUSA a pergunta: sem
classificação, ela não entra. O relatório sai do mesmo jeito; só a pergunta extra fica de fora.

O que este guardrail devolve vai para a auditoria e para os avisos que o redator lê: por isso os
motivos nunca repetem a pergunta, o texto de uma exceção (só o nome da classe) nem um motivo do
classificador com dado pessoal ou com instrução dirigida ao modelo.
"""

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from srag_agente.guardrails.conteudo_externo import limpar_texto, tem_dado_pessoal, tem_injecao
from srag_agente.guardrails.padroes import mascarar_pii, normalizar_unicode

LIMITE_PERGUNTA = 500
LIMITE_MOTIVO = 300


class ClassificacaoEscopo(BaseModel):
    # O erro de validação não repete o texto do LLM (que pode ecoar a pergunta com dado pessoal).
    model_config = ConfigDict(hide_input_in_errors=True)

    no_escopo: bool = Field(description="True se a pergunta é sobre a situação epidemiológica.")
    motivo: str = Field(max_length=LIMITE_MOTIVO)


class ResultadoGuardrail(BaseModel):
    aprovado: bool
    motivos: list[str] = []
    tokens_entrada: int = 0
    tokens_saida: int = 0
    # Quem classificou (principal ou reserva), segundo o provedor; None se não informado.
    modelo: str | None = None
    # A pergunta exatamente como foi conferida (normalizada); só existe quando aprovada. É ela
    # que deve seguir para o agente, e não o texto cru.
    pergunta: str | None = None


# (classificação, (tokens de entrada, tokens de saída), modelo que respondeu ou None)
Classificador = Callable[[str], tuple[ClassificacaoEscopo, tuple[int, int], str | None]]


def _recusa(
    motivo: str, tokens: tuple[int, int] = (0, 0), modelo: str | None = None
) -> ResultadoGuardrail:
    return ResultadoGuardrail(
        aprovado=False,
        motivos=[motivo],
        tokens_entrada=tokens[0],
        tokens_saida=tokens[1],
        modelo=modelo,
    )


def _tokens_validos(tokens: object) -> tuple[int, int] | None:
    if not (isinstance(tokens, tuple) and len(tokens) == 2):
        return None
    if all(isinstance(t, int) and not isinstance(t, bool) and t >= 0 for t in tokens):
        return tokens[0], tokens[1]
    return None


def _motivo_publicavel(motivo: str) -> str:
    """O motivo do LLM vira aviso lido pelo redator: sem instrução, sem dado pessoal, sem tag."""
    if tem_injecao(motivo):
        return "[motivo omitido: contém instrução]"
    # Mascara DEPOIS de desfazer as entidades HTML ("123&#46;456…" só vira CPF desfeito) e corta
    # só no fim: cortar antes deixaria um pedaço de documento que o padrão já não reconhece.
    return mascarar_pii(limpar_texto(motivo, limite=None))[0][:LIMITE_MOTIVO]


def verificar_entrada(pergunta: str | None, classificador: Classificador) -> ResultadoGuardrail:
    bruta = (pergunta or "").strip()
    if len(bruta) > LIMITE_PERGUNTA:  # antes de normalizar: não processa texto gigante
        return _recusa("pergunta_longa")
    texto = normalizar_unicode(bruta).strip()  # NFKC pode expandir e o Cf pode esvaziar
    if not texto:
        return _recusa("pergunta_vazia")
    if len(texto) > LIMITE_PERGUNTA:
        return _recusa("pergunta_longa")
    if tem_injecao(texto):  # texto, entidades HTML desfeitas e tags removidas
        return _recusa("injecao")
    # O dado pessoal é procurado também com as entidades HTML desfeitas ("123&#46;456&#46;…").
    if tem_dado_pessoal(texto):
        return _recusa("dado_pessoal")
    try:
        classificacao, tokens_brutos, modelo_bruto = classificador(texto)
    except Exception as erro:  # falha fechada; o texto da exceção pode repetir a pergunta
        # resposta mal formada (ErroRedacao) já custou: os tokens que o provedor informou contam
        gastos = _tokens_validos(getattr(erro, "tokens", None)) or (0, 0)
        return _recusa(f"classificador_indisponivel: {type(erro).__name__}", gastos)
    tokens = _tokens_validos(tokens_brutos)
    # o nome do modelo só informa a auditoria: estranho ou ausente vira None, não recusa
    modelo = modelo_bruto if isinstance(modelo_bruto, str) and modelo_bruto else None
    formato_ok = isinstance(classificacao, ClassificacaoEscopo) and isinstance(
        classificacao.no_escopo,
        bool,  # model_construct pula a validação
    )
    if not formato_ok or tokens is None:
        return _recusa("classificador_indisponivel: resposta fora do formato")
    if not classificacao.no_escopo:
        motivo = f"fora_do_escopo: {_motivo_publicavel(classificacao.motivo)}"
        return _recusa(motivo, tokens, modelo)
    return ResultadoGuardrail(
        aprovado=True,
        tokens_entrada=tokens[0],
        tokens_saida=tokens[1],
        modelo=modelo,
        pergunta=texto,
    )
