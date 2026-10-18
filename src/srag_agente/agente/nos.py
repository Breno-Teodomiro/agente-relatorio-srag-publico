"""Nós do grafo do agente. Cada nó registra na auditoria o que decidiu e por quê."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable

from srag_agente.agente.cobertura import faltantes
from srag_agente.agente.contexto import montar_contexto
from srag_agente.agente.estado import EstadoAgente
from srag_agente.agente.llm import (
    ErroRedacao,
    Redator,
    modelo_da_resposta,
    pergunta_como_dado,
    uso_tokens,
)
from srag_agente.agente.prompts import carregar_prompt
from srag_agente.auditoria.registro import Auditoria
from srag_agente.ferramentas.registro import ArgumentosInvalidos, Ferramenta, executar
from srag_agente.guardrails.entrada import Classificador, verificar_entrada
from srag_agente.guardrails.limites import Limites, motivo_de_parada
from srag_agente.guardrails.padroes import mascarar_pii
from srag_agente.guardrails.saida import verificar_saida
from srag_agente.metricas.repositorio import DadosIndisponiveis
from srag_agente.modelos import TextoRelatorio, numerar_noticias

AVISO_COBERTURA = "Evidências incompletas"
ERRO_JSON_INVALIDO = (
    "Argumentos inválidos: o JSON da chamada não pôde ser lido. Reenvie a chamada com JSON válido."
)
ERRO_DESCONHECIDA = "Ferramenta desconhecida: use só as ferramentas disponíveis."


def _ms(inicio: float) -> int:
    return int((perf_counter() - inicio) * 1000)


def _identificar(texto: TextoRelatorio) -> dict:
    """Tamanho e SHA-256 do texto (já mascarado), sem o conteúdo: identifica o texto retido."""
    serializado = json.dumps(texto.model_dump(), ensure_ascii=False, sort_keys=True)
    return {
        "caracteres": sum(len(secao) for secao in texto.secoes().values()),
        "sha256": hashlib.sha256(serializado.encode("utf-8")).hexdigest(),
    }


@dataclass
class Dependencias:
    modelo_orquestrador: Runnable
    redator: Redator
    classificador: Classificador
    ferramentas: dict[str, Ferramenta]
    auditoria: Auditoria
    limites: Limites
    relogio: Callable[[], float] = perf_counter  # injetável para testar o teto de segundos


def _tarefa(estado: EstadoAgente) -> str:
    linhas = [
        f"UF do relatório: {estado['uf']} ('BR' = Brasil inteiro).",
        f"Data de execução (hoje): {estado['data_execucao'].isoformat()}.",
        "Reúna todas as evidências obrigatórias.",
    ]
    if pergunta := estado.get("pergunta_extra"):
        # mesmo tratamento do redator: tags da pergunta saem, ela não fecha o bloco antes da hora
        linhas.append(f"Pergunta complementar do usuário:\n{pergunta_como_dado(pergunta)}")
    return "\n".join(linhas)


class NosAgente:
    """Os nós de UMA execução: o relógio do teto de segundos começa na construção.

    `construir_grafo` cria um NosAgente a cada `executar_agente`, então cada execução tem o
    seu início.
    """

    def __init__(self, deps: Dependencias) -> None:
        self.d = deps
        self._inicio = deps.relogio()

    @staticmethod
    def _tokens(estado: EstadoAgente) -> int:
        return estado["tokens_entrada"] + estado["tokens_saida"]

    def _parada(self, estado: EstadoAgente, chamadas_no_lote: int = 0) -> str | None:
        """Motivo para parar (chamadas, tokens ou segundos), ou None. Único ponto de conferência.

        É conferido entre passos: nas rotas, na cobertura e antes de cada chamada de um lote de
        ferramentas. NÃO interrompe uma chamada já em andamento (LLM, banco ou busca travados):
        quem segura essas são os timeouts dos próprios clientes (ex.: `llm_timeout_s`).
        """
        return motivo_de_parada(
            self.d.limites,
            estado["chamadas_ferramenta"] + chamadas_no_lote,
            self._tokens(estado),
            max(0.0, self.d.relogio() - self._inicio),
        )

    # ---- 1. guardrail de entrada -------------------------------------------------------------
    def guardrail_entrada(self, estado: EstadoAgente) -> dict:
        pergunta = estado.get("pergunta_extra")
        if not pergunta:
            return {}
        resultado = verificar_entrada(pergunta, self.d.classificador)
        self.d.auditoria.registrar(
            "guardrail_entrada",
            "guardrail",
            argumentos={"pergunta": mascarar_pii(pergunta)[0][:500]},
            resultado=resultado.model_dump(exclude={"pergunta"}),
            tokens_entrada=resultado.tokens_entrada,
            tokens_saida=resultado.tokens_saida,
        )
        atualizacao = {
            "tokens_entrada": resultado.tokens_entrada,
            "tokens_saida": resultado.tokens_saida,
        }
        if resultado.aprovado:
            # segue a pergunta como foi conferida (normalizada), não o texto cru
            return {**atualizacao, "pergunta_extra": resultado.pergunta}
        motivos = "; ".join(resultado.motivos)
        return {
            **atualizacao,
            "pergunta_extra": None,
            "avisos": [f"Pergunta complementar recusada pelo guardrail de entrada ({motivos})."],
        }

    # ---- 2. orquestrador ---------------------------------------------------------------------
    def orquestrador(self, estado: EstadoAgente) -> dict:
        novas = []
        if not estado["messages"]:
            novas = [SystemMessage(carregar_prompt("orquestrador")), HumanMessage(_tarefa(estado))]
        inicio = perf_counter()
        resposta = self.d.modelo_orquestrador.invoke([*estado["messages"], *novas])
        tokens_entrada, tokens_saida = uso_tokens(resposta)
        self.d.auditoria.registrar(
            "orquestrador",
            "llm",
            resultado={
                "modelo": modelo_da_resposta(resposta),  # com a reserva, o nome dela
                "ferramentas_planejadas": [
                    {"ferramenta": c["name"], "motivo": c["args"].get("motivo")}
                    for c in resposta.tool_calls
                ],
                # argumentos de chamada inválida são texto cru do LLM: só o nome é registrado
                "chamadas_invalidas": [c.get("name") for c in resposta.invalid_tool_calls],
                # sem corte aqui: a auditoria mascara o dado pessoal e só DEPOIS limita o tamanho
                "texto": str(resposta.content),
            },
            duracao_ms=_ms(inicio),
            tokens_entrada=tokens_entrada,
            tokens_saida=tokens_saida,
        )
        return {
            "messages": [*novas, resposta],
            "tokens_entrada": tokens_entrada,
            "tokens_saida": tokens_saida,
        }

    def rota_orquestrador(
        self, estado: EstadoAgente
    ) -> Literal["ferramentas", "verificar_cobertura"]:
        ultima = estado["messages"][-1]
        # chamada com JSON malformado cai em invalid_tool_calls, mas vai no payload seguinte
        # como tool_call: também precisa de resposta, senão a API recusa a próxima volta
        pediu_ferramentas = isinstance(ultima, AIMessage) and bool(
            ultima.tool_calls or ultima.invalid_tool_calls
        )
        if pediu_ferramentas and self._parada(estado) is None:
            return "ferramentas"
        return "verificar_cobertura"

    # ---- 3. ferramentas ----------------------------------------------------------------------
    def _registrar_falha(self, causa: Exception | None, **campos) -> None:
        """Grava o erro da ferramenta; se a própria auditoria falhar, a causa original não some."""
        try:
            self.d.auditoria.registrar("ferramentas", "ferramenta", **campos)
        except Exception as falha_auditoria:
            raise falha_auditoria from causa

    def _executar_chamada(self, chamada: dict) -> tuple[str, bool, object]:
        """Executa uma chamada válida. Devolve (texto para o LLM, erro?, evidência ou None).

        O que vai ao LLM é só o que é nosso: mensagem de ArgumentosInvalidos (já limpa) e de
        DadosIndisponiveis. Exceção inesperada (ex.: psycopg com host, porta e usuário) vai ao
        LLM só pelo tipo; o texto completo fica na auditoria, que mascara o dado pessoal.
        """
        nome, argumentos = chamada["name"], chamada["args"]
        ferramenta = self.d.ferramentas.get(nome)
        inicio = perf_counter()
        causa: Exception | None = None
        campos: dict = {}
        try:
            if ferramenta is None:
                causa, erro = LookupError("ferramenta desconhecida"), ERRO_DESCONHECIDA
                para_llm = erro
            else:
                evidencia = executar(ferramenta, argumentos)
        except ArgumentosInvalidos as exc:
            # a mensagem já vem limpa; argumentos e motivo crus NÃO vão para a auditoria
            causa, texto = exc, str(exc)
            erro = para_llm = texto[:1].upper() + texto[1:]
            campos = {"ferramenta": nome}
        except DadosIndisponiveis as exc:  # mensagem nossa: segue inteira ao orquestrador
            causa = exc
            erro = para_llm = f"Erro ao executar {nome}: {type(exc).__name__}: {exc}"
            # os argumentos passaram na validação (campos e tipos do esquema); motivo que não
            # seja texto a própria auditoria converte e mascara
            campos = {"ferramenta": nome, "argumentos": argumentos,
                      "motivo": argumentos.get("motivo")}  # fmt: skip
        except Exception as exc:  # qualquer falha vira resposta para o orquestrador decidir
            causa = exc
            para_llm = f"Erro ao executar {nome}: {type(exc).__name__}."
            erro = f"Erro ao executar {nome}: {type(exc).__name__}: {exc}"
            campos = {"ferramenta": nome, "motivo": argumentos.get("motivo")}
        if causa is not None:
            self._registrar_falha(causa, duracao_ms=_ms(inicio), erro=erro, **campos)
            return para_llm, True, None
        self.d.auditoria.registrar(
            "ferramentas",
            "ferramenta",
            ferramenta=nome,
            argumentos=evidencia.argumentos,
            motivo=evidencia.argumentos["motivo"],
            resultado=evidencia.dados,
            duracao_ms=_ms(inicio),
        )
        return evidencia.para_llm(), False, evidencia

    def _chamada_invalida(self, chamada: dict) -> tuple[str, bool, object]:
        """JSON malformado: responde com erro fixo. Os argumentos (texto cru) não são gravados."""
        nome = chamada.get("name")
        conhecida = nome if nome in self.d.ferramentas else None
        self._registrar_falha(None, ferramenta=conhecida, erro=ERRO_JSON_INVALIDO)
        return ERRO_JSON_INVALIDO, True, None

    def ferramentas(self, estado: EstadoAgente) -> dict:
        """Responde TODA chamada do lote (válida, inválida ou não executada) com um ToolMessage.

        A API exige uma resposta por tool_call_id antes da próxima mensagem. Chamada inválida
        conta no limite de chamadas. Antes de cada chamada confere o teto (chamadas, tokens e
        segundos); o que passar do teto recebe "Não executada".
        """
        ultima = estado["messages"][-1]
        lote = [(c, self._executar_chamada) for c in ultima.tool_calls]
        lote += [(c, self._chamada_invalida) for c in ultima.invalid_tool_calls]
        mensagens, evidencias, executadas = [], [], 0
        for chamada, tratar in lote:
            if parada := self._parada(estado, executadas):
                aviso = f"Não executada: {parada}."
                nome = chamada.get("name")
                self.d.auditoria.registrar(
                    "ferramentas",
                    "decisao",
                    ferramenta=nome if nome in self.d.ferramentas else None,
                    erro=aviso,  # o motivo do LLM não é gravado aqui: pode nem ser texto
                )
                mensagens.append(ToolMessage(aviso, tool_call_id=chamada["id"], status="error"))
                continue
            executadas += 1
            texto, falhou, evidencia = tratar(chamada)
            if evidencia is not None:
                evidencias.append(evidencia)
            mensagens.append(
                ToolMessage(
                    texto, tool_call_id=chamada["id"], status="error" if falhou else "success"
                )
            )
        return {"messages": mensagens, "evidencias": evidencias, "chamadas_ferramenta": executadas}

    def rota_ferramentas(
        self, estado: EstadoAgente
    ) -> Literal["orquestrador", "verificar_cobertura"]:
        """Com um teto atingido, não paga outra volta ao orquestrador: vai direto à cobertura."""
        return "orquestrador" if self._parada(estado) is None else "verificar_cobertura"

    # ---- 4. cobertura ------------------------------------------------------------------------
    def verificar_cobertura(self, estado: EstadoAgente) -> dict:
        falta = faltantes(estado["evidencias"], estado["uf"])
        parada = self._parada(estado)
        voltas = estado["voltas_cobertura"]
        if not falta:
            decisao, atualizacao = "evidencias_completas", {"rota_cobertura": "redator"}
        elif parada is None and voltas < self.d.limites.max_voltas_cobertura:
            decisao = "pedir_evidencias_faltantes"
            atualizacao = {
                "rota_cobertura": "orquestrador",
                "voltas_cobertura": voltas + 1,
                "messages": [
                    HumanMessage(
                        f"Ainda faltam evidências: {', '.join(falta)}. "
                        "Chame as ferramentas necessárias."
                    )
                ],
            }
        else:
            decisao = "seguir_com_evidencias_incompletas"
            causa = parada or f"orquestrador não obteve tudo após {voltas} voltas"
            atualizacao = {
                "rota_cobertura": "redator",
                "cobertura_incompleta": True,
                "avisos": [f"{AVISO_COBERTURA}: faltou {', '.join(falta)} ({causa})."],
            }
        self.d.auditoria.registrar(
            "verificar_cobertura",
            "decisao",
            resultado={"decisao": decisao, "faltantes": falta, "limite": parada},
        )
        return atualizacao

    def rota_cobertura(self, estado: EstadoAgente) -> Literal["orquestrador", "redator"]:
        return estado["rota_cobertura"]

    # ---- 5. redator --------------------------------------------------------------------------
    def redator(self, estado: EstadoAgente) -> dict:
        """Redige o texto; roda mesmo depois de um teto (sai o relatório parcial).

        Saída fora do formato (ErroRedacao) vira uma violação de texto FIXO (só o tipo do erro):
        a saída crua pode repetir o que o modelo escreveu e a violação chega ao relatório. O
        detalhe fica na auditoria, mascarado.

        Se a ÚLTIMA tentativa der ErroRedacao, a execução vai a revisão humana sem texto: o
        relatório sai igual ao de um texto reprovado (que fica retido), só sem o hash dele na
        trilha.
        """
        tentativa = estado["tentativas_redacao"] + 1
        contexto = montar_contexto(
            estado["evidencias"],
            uf=estado["uf"],
            data_execucao=estado["data_execucao"],
            pergunta_extra=estado.get("pergunta_extra"),
            violacoes_anteriores=estado["violacoes"],
            avisos=estado["avisos"],
        )
        inicio = perf_counter()
        try:
            texto, (tokens_entrada, tokens_saida), modelo = self.d.redator(contexto)
        except ErroRedacao as exc:
            self.d.auditoria.registrar(
                "redator",
                "llm",
                resultado={"tentativa": tentativa, "modelo": exc.modelo},
                duracao_ms=_ms(inicio),
                erro=str(exc),  # sem corte: a auditoria mascara e só depois limita o tamanho
            )
            return {
                "texto": None,
                "tentativas_redacao": tentativa,
                "violacoes": [
                    "saida_invalida: o redator devolveu texto fora do formato estruturado "
                    f"({type(exc).__name__})."
                ],
            }
        self.d.auditoria.registrar(
            "redator",
            "llm",
            resultado={"tentativa": tentativa, "modelo": modelo, "secoes": list(texto.secoes())},
            duracao_ms=_ms(inicio),
            tokens_entrada=tokens_entrada,
            tokens_saida=tokens_saida,
        )
        return {
            "texto": texto,
            "tentativas_redacao": tentativa,
            "tokens_entrada": tokens_entrada,
            "tokens_saida": tokens_saida,
        }

    # ---- 6. guardrail de saída ---------------------------------------------------------------
    def guardrail_saida(self, estado: EstadoAgente) -> dict:
        """Confere o texto e decide a situação.

        Os avisos (ex.: notícia sem data citada, ADR 0007) vão à auditoria em TODA tentativa, mas
        ao estado (e daí ao relatório) só os do texto final: `avisos` acumula entre os nós, e o
        de uma tentativa refeita se repetiria ou sobraria sem a citação que o gerou.
        """
        texto = estado["texto"]
        if texto is None:
            violacoes, pii, avisos = estado["violacoes"], [], []
        else:
            resultado = verificar_saida(
                texto, estado["evidencias"], numerar_noticias(estado["evidencias"])
            )
            texto, violacoes, pii, avisos = (
                resultado.texto,
                resultado.violacoes,
                resultado.dados_pessoais_mascarados,
                resultado.avisos,
            )
        if not violacoes:
            status = "parcial" if estado["cobertura_incompleta"] else "ok"
        elif estado["tentativas_redacao"] < self.d.limites.max_tentativas_redacao:
            status = "em_andamento"
        else:
            status = "revisao_humana"
        retido = status == "revisao_humana" and texto is not None
        self.d.auditoria.registrar(
            "guardrail_saida",
            "guardrail",
            resultado={
                "aprovado": not violacoes,
                # antes das listas: um resultado longo é truncado pelo fim (`resumir`)
                **({"texto_retido": _identificar(texto)} if retido else {}),
                "violacoes": violacoes,
                "dados_pessoais_mascarados": pii,
                "avisos": avisos,
                "status": status,
            },
        )
        # Em revisão humana o texto reprovado fica retido: não segue ao relatório (que publica só
        # o que vem do SQL, a faixa e as violações) nem à trilha (que guarda tamanho e hash).
        atualizacao = {"texto": None if retido else texto, "violacoes": violacoes, "status": status}
        if status != "em_andamento":
            atualizacao["avisos"] = avisos
        return atualizacao

    def rota_saida(self, estado: EstadoAgente) -> Literal["redator", "fim"]:
        return "redator" if estado["status"] == "em_andamento" else "fim"
