"""Nós do grafo do agente. Cada nó registra na auditoria o que decidiu e por quê."""

from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable

from srag_agente.agente.cobertura import faltantes
from srag_agente.agente.contexto import montar_contexto
from srag_agente.agente.estado import EstadoAgente
from srag_agente.agente.llm import ErroRedacao, Redator, uso_tokens
from srag_agente.agente.prompts import carregar_prompt
from srag_agente.auditoria.registro import Auditoria
from srag_agente.ferramentas.registro import ArgumentosInvalidos, Ferramenta, executar
from srag_agente.guardrails.entrada import Classificador, verificar_entrada
from srag_agente.guardrails.limites import Limites, chamadas_restantes, motivo_de_parada
from srag_agente.guardrails.padroes import mascarar_pii
from srag_agente.guardrails.saida import verificar_saida
from srag_agente.modelos import numerar_noticias

AVISO_COBERTURA = "Evidências incompletas"


def _ms(inicio: float) -> int:
    return int((perf_counter() - inicio) * 1000)


@dataclass
class Dependencias:
    modelo_orquestrador: Runnable
    redator: Redator
    classificador: Classificador
    ferramentas: dict[str, Ferramenta]
    auditoria: Auditoria
    limites: Limites


def _tarefa(estado: EstadoAgente) -> str:
    linhas = [
        f"UF do relatório: {estado['uf']} ('BR' = Brasil inteiro).",
        f"Data de execução (hoje): {estado['data_execucao'].isoformat()}.",
        "Reúna todas as evidências obrigatórias.",
    ]
    if pergunta := estado.get("pergunta_extra"):
        linhas.append(
            f"Pergunta complementar do usuário:\n<pergunta_usuario>{pergunta}</pergunta_usuario>"
        )
    return "\n".join(linhas)


class NosAgente:
    def __init__(self, deps: Dependencias) -> None:
        self.d = deps

    @staticmethod
    def _tokens(estado: EstadoAgente) -> int:
        return estado["tokens_entrada"] + estado["tokens_saida"]

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
                "modelo": resposta.response_metadata.get("model_name"),
                "ferramentas_planejadas": [
                    {"ferramenta": c["name"], "motivo": c["args"].get("motivo")}
                    for c in resposta.tool_calls
                ],
                "texto": str(resposta.content)[:500],
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
        pediu_ferramentas = isinstance(ultima, AIMessage) and bool(ultima.tool_calls)
        dentro_do_limite = (
            motivo_de_parada(self.d.limites, estado["chamadas_ferramenta"], self._tokens(estado))
            is None
        )
        return "ferramentas" if pediu_ferramentas and dentro_do_limite else "verificar_cobertura"

    # ---- 3. ferramentas ----------------------------------------------------------------------
    def _registrar_falha(self, causa: Exception, **campos) -> None:
        """Grava o erro da ferramenta; se a própria auditoria falhar, a causa original não some."""
        try:
            self.d.auditoria.registrar("ferramentas", "ferramenta", **campos)
        except Exception as falha_auditoria:
            raise falha_auditoria from causa

    def ferramentas(self, estado: EstadoAgente) -> dict:
        restantes = chamadas_restantes(self.d.limites, estado["chamadas_ferramenta"])
        mensagens, evidencias, executadas = [], [], 0
        for chamada in estado["messages"][-1].tool_calls:
            nome, argumentos = chamada["name"], chamada["args"]
            ferramenta = self.d.ferramentas.get(nome)
            if executadas >= restantes:
                aviso = "Não executada: limite de chamadas de ferramenta atingido."
                self.d.auditoria.registrar(
                    "ferramentas",
                    "decisao",
                    ferramenta=nome if ferramenta else None,
                    motivo=argumentos.get("motivo"),
                    erro=aviso,
                )
                mensagens.append(ToolMessage(aviso, tool_call_id=chamada["id"], status="error"))
                continue
            executadas += 1
            inicio = perf_counter()
            causa: Exception | None = None
            campos: dict = {}
            try:
                if ferramenta is None:
                    causa = LookupError("ferramenta desconhecida")
                    erro = "Ferramenta desconhecida: use só as ferramentas disponíveis."
                else:
                    evidencia = executar(ferramenta, argumentos)
            except ArgumentosInvalidos as exc:
                # a mensagem já vem limpa; argumentos e motivo crus NÃO vão para a auditoria
                causa, texto = exc, str(exc)
                erro = texto[:1].upper() + texto[1:]
                campos = {"ferramenta": nome}
            except Exception as exc:  # qualquer falha vira resposta para o orquestrador decidir
                causa = exc
                erro = f"Erro ao executar {nome}: {type(exc).__name__}: {exc}"
                campos = {
                    "ferramenta": nome,
                    "argumentos": argumentos,
                    "motivo": argumentos.get("motivo"),
                }
            if causa is not None:
                self._registrar_falha(causa, duracao_ms=_ms(inicio), erro=erro, **campos)
                mensagens.append(ToolMessage(erro, tool_call_id=chamada["id"], status="error"))
                continue
            evidencias.append(evidencia)
            self.d.auditoria.registrar(
                "ferramentas",
                "ferramenta",
                ferramenta=nome,
                argumentos=evidencia.argumentos,
                motivo=evidencia.argumentos["motivo"],
                resultado=evidencia.dados,
                duracao_ms=_ms(inicio),
            )
            mensagens.append(ToolMessage(evidencia.para_llm(), tool_call_id=chamada["id"]))
        return {"messages": mensagens, "evidencias": evidencias, "chamadas_ferramenta": executadas}

    # ---- 4. cobertura ------------------------------------------------------------------------
    def verificar_cobertura(self, estado: EstadoAgente) -> dict:
        falta = faltantes(estado["evidencias"], estado["uf"])
        parada = motivo_de_parada(
            self.d.limites, estado["chamadas_ferramenta"], self._tokens(estado)
        )
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
            texto, (tokens_entrada, tokens_saida) = self.d.redator(contexto)
        except ErroRedacao as exc:
            self.d.auditoria.registrar(
                "redator",
                "llm",
                resultado={"tentativa": tentativa},
                duracao_ms=_ms(inicio),
                erro=str(exc)[:500],
            )
            return {
                "texto": None,
                "tentativas_redacao": tentativa,
                "violacoes": [f"saida_invalida: {str(exc)[:200]}"],
            }
        self.d.auditoria.registrar(
            "redator",
            "llm",
            resultado={"tentativa": tentativa, "secoes": list(texto.secoes())},
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
        texto = estado["texto"]
        if texto is None:
            violacoes, pii = estado["violacoes"], []
        else:
            resultado = verificar_saida(
                texto, estado["evidencias"], numerar_noticias(estado["evidencias"])
            )
            texto, violacoes, pii = (
                resultado.texto,
                resultado.violacoes,
                resultado.dados_pessoais_mascarados,
            )
        if not violacoes:
            status = "parcial" if estado["cobertura_incompleta"] else "ok"
        elif estado["tentativas_redacao"] < self.d.limites.max_tentativas_redacao:
            status = "em_andamento"
        else:
            status = "revisao_humana"
        self.d.auditoria.registrar(
            "guardrail_saida",
            "guardrail",
            resultado={
                "aprovado": not violacoes,
                "violacoes": violacoes,
                "dados_pessoais_mascarados": pii,
                "status": status,
            },
        )
        return {"texto": texto, "violacoes": violacoes, "status": status}

    def rota_saida(self, estado: EstadoAgente) -> Literal["redator", "fim"]:
        return "redator" if estado["status"] == "em_andamento" else "fim"
