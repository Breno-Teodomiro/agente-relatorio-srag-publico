"""Trilha de auditoria: memória (para o relatório), Postgres (consulta) e JSONL (anexo).

Regras da trilha, valendo nas três formas:
- só cresce enquanto a execução está aberta: nada se registra antes de `iniciar_execucao`
  nem depois de `finalizar_execucao`, e a execução se encerra uma única vez;
- um evento só entra na memória depois de gravado (sem evento "fantasma" nem buraco na
  sequência quando o banco falha);
- o conteúdo é sempre JSON válido e de tamanho limitado (`resumir`), sem dado pessoal
  (CPF, cartão SUS, telefone, e-mail… mascarados nos parâmetros da execução e em argumentos,
  resultado, motivo e erro de cada evento).
O banco repete essas regras por conta própria (04_auditoria.sql): o registro não é a única
barreira.
"""

import json
import math
import os
import tempfile
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, get_args
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from srag_agente.guardrails.padroes import MASCARA, mascarar_pii

TipoEvento = Literal["llm", "ferramenta", "guardrail", "decisao"]
# Situação final de uma execução (a mesma lista do CHECK em 04_auditoria.sql).
StatusFinal = Literal["ok", "parcial", "revisao_humana", "erro"]
STATUS_FINAIS: frozenset[str] = frozenset(get_args(StatusFinal))
LIMITE_RESULTADO = 2000
LIMITE_CAMPO_PRESERVADO = 120  # campo curto que sobrevive, como chave própria, ao resumo truncado


def _agora() -> str:
    return datetime.now(UTC).isoformat()


def _json_seguro(valor: Any) -> Any:
    """Troca NaN/Infinity (fora do JSON, recusados pelo jsonb) por texto, em qualquer nível."""
    if isinstance(valor, float) and not math.isfinite(valor):
        return str(valor)
    if isinstance(valor, dict):
        return {chave: _json_seguro(v) for chave, v in valor.items()}
    if isinstance(valor, list | tuple | set | frozenset):
        return [_json_seguro(v) for v in valor]
    return valor


def _para_json(valor: Any) -> str:
    return json.dumps(_json_seguro(valor), ensure_ascii=False, default=str, allow_nan=False)


def sem_pii(valor: Any) -> Any:
    """Mascara dado pessoal em textos, chaves e inteiros com cara de documento, em todo nível.

    É o mascaramento único do projeto para estruturas: a trilha (`resumir`, parâmetros) e a
    máscara do Langfuse (`nucleo.mascara_observabilidade`). Tupla vira lista; objeto
    desconhecido (mensagem do LangChain…) vira texto, para não passar sem conferência.
    """
    if isinstance(valor, dict):
        return {sem_pii(str(chave)): sem_pii(v) for chave, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [sem_pii(v) for v in valor]
    if valor is None or isinstance(valor, bool | float):
        return valor
    if isinstance(valor, int):  # CPF ou cartão SUS sem pontuação
        return MASCARA if mascarar_pii(str(valor))[1] else valor
    return mascarar_pii(valor if isinstance(valor, str) else str(valor))[0]


def _curto(valor: Any) -> bool:
    if valor is None or isinstance(valor, bool | int | float):
        return True
    return isinstance(valor, str) and len(valor) <= LIMITE_CAMPO_PRESERVADO


def resumir(valor: Any) -> Any:
    """Mantém o valor se for pequeno; senão guarda um resumo truncado (sem perder o tamanho).

    O dado pessoal é mascarado ANTES do corte: um CPF na fronteira não sobra pela metade. Num
    dicionário truncado, os campos curtos de primeiro nível (número, booleano, texto de até
    ``LIMITE_CAMPO_PRESERVADO`` caracteres: o ``modelo`` do orquestrador, a ``janela`` da busca)
    continuam como chave própria, ao lado do resumo: o anexo do relatório os lê de lá.
    """
    if valor is None:
        return None
    limpo = sem_pii(json.loads(_para_json(valor)))
    texto = _para_json(limpo)
    if len(texto) <= LIMITE_RESULTADO:
        return json.loads(texto)
    preservados = (
        {chave: v for chave, v in limpo.items() if _curto(v)} if isinstance(limpo, dict) else {}
    )
    # o resumo vem por último: um campo do valor com o mesmo nome não o substitui
    return {**preservados, "resumo_truncado": texto[:LIMITE_RESULTADO],
            "tamanho_original": len(texto)}  # fmt: skip


def descrever_erro(erro: BaseException) -> str:
    """Texto de um erro para a trilha ou o terminal (sem mascarar: quem mostra mascara).

    Do ValidationError do pydantic, só campo e mensagem: o `input` recebido pode ser a pergunta
    crua do usuário ou um segredo.
    """
    if isinstance(erro, ValidationError):
        campos = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or '(raiz)'}: {e['msg']}"
            for e in erro.errors(include_url=False, include_input=False, include_context=False)
        )
        return f"ValidationError ({erro.title}): {campos}"
    return f"{type(erro).__name__}: {erro}"


def _cortar(texto: object) -> str | None:
    """Motivo e erro: mascarados e cortados, nessa ordem.

    O motivo pode vir cru do LLM (caminho de erro da ferramenta): o que não for texto vira texto
    aqui, para não derrubar o nó (motivo=12345) nem passar sem mascarar.
    """
    if texto is None:
        return None
    return mascarar_pii(texto if isinstance(texto, str) else str(texto))[0][:LIMITE_RESULTADO]


def _conferir_consumo(tokens_entrada: int, tokens_saida: int, custo_usd: float,
                      total_casos: int | None) -> None:  # fmt: skip
    if tokens_entrada < 0 or tokens_saida < 0:
        raise ValueError("tokens não podem ser negativos")
    if not math.isfinite(custo_usd) or custo_usd < 0:
        raise ValueError(f"custo_usd inválido: {custo_usd!r}")
    if total_casos is not None and total_casos < 0:
        raise ValueError("total_casos não pode ser negativo")


class Auditoria:
    """Trilha em memória. As subclasses gravam fora do processo pelos ganchos `_persistir_*`."""

    def __init__(self) -> None:
        self.id_execucao: UUID | None = None
        self.execucao: dict[str, Any] = {}
        self.eventos: list[dict[str, Any]] = []
        # O grafo pode rodar ferramentas em paralelo: a sequência não pode se repetir.
        self._trava = threading.Lock()

    def __repr__(self) -> str:
        situacao = self.execucao.get("status", "não iniciada")
        return f"<{type(self).__name__} {self.id_execucao} {situacao} eventos={len(self.eventos)}>"

    @property
    def encerrada(self) -> bool:
        return "fim" in self.execucao

    def _exigir_aberta(self) -> None:
        if self.id_execucao is None:
            raise RuntimeError("chame iniciar_execucao antes de registrar")
        if self.encerrada:
            raise RuntimeError(f"execução {self.id_execucao} já encerrada: a trilha não muda")

    def iniciar_execucao(
        self, *, provedor: str, modelo: str, parametros: dict, versao_codigo: str,
        hash_prompts: str,
    ) -> UUID:  # fmt: skip
        with self._trava:
            if self.id_execucao is not None:
                raise RuntimeError(f"execução {self.id_execucao} já iniciada neste registro")
            self.id_execucao = uuid4()
            self.execucao = {
                "id_execucao": str(self.id_execucao),
                "inicio": _agora(),
                "status": "em_andamento",
                "provedor": provedor,
                "modelo": modelo,
                # o núcleo já mascara a pergunta antes de cortá-la; aqui é a segunda barreira
                "parametros": sem_pii(json.loads(_para_json(parametros))),
                "versao_codigo": versao_codigo,
                "hash_prompts": hash_prompts,
            }
            try:
                self.execucao.update(self._persistir_inicio())
            except BaseException:
                self.id_execucao, self.execucao = None, {}
                raise
            return self.id_execucao

    def registrar(
        self,
        no: str,
        tipo: TipoEvento,
        *,
        ferramenta: str | None = None,
        argumentos: Any = None,
        motivo: str | None = None,
        resultado: Any = None,
        duracao_ms: int | None = None,
        tokens_entrada: int | None = None,
        tokens_saida: int | None = None,
        erro: str | None = None,
    ) -> dict[str, Any]:
        with self._trava:
            self._exigir_aberta()
            evento = {
                "sequencia": len(self.eventos) + 1,
                "momento": _agora(),
                "no": no,
                "tipo": tipo,
                "ferramenta": ferramenta,
                "argumentos": resumir(argumentos),
                "motivo": _cortar(motivo),
                "resultado": resumir(resultado),
                "duracao_ms": duracao_ms,
                "tokens_entrada": tokens_entrada,
                "tokens_saida": tokens_saida,
                "erro": _cortar(erro),
            }
            evento.update(self._persistir_evento(evento))
            self.eventos.append(evento)
            return evento

    def finalizar_execucao(
        self, *, status: StatusFinal, data_referencia: date | None, tokens_entrada: int,
        tokens_saida: int, custo_usd: float, total_casos: int | None = None,
    ) -> None:  # fmt: skip
        """Encerra a execução uma única vez. `total_casos` é a contagem da silver que ela viu."""
        if status not in STATUS_FINAIS:
            raise ValueError(f"status final inválido: {status!r} (use {sorted(STATUS_FINAIS)})")
        _conferir_consumo(tokens_entrada, tokens_saida, custo_usd, total_casos)
        with self._trava:
            self._exigir_aberta()
            encerramento = {
                "fim": _agora(),
                "status": status,
                "data_referencia": data_referencia.isoformat() if data_referencia else None,
                "total_casos": total_casos,
                "tokens_entrada": tokens_entrada,
                "tokens_saida": tokens_saida,
                "custo_usd": round(custo_usd, 6),
            }
            encerramento.update(self._persistir_fim(encerramento))
            self.execucao.update(encerramento)

    def gravar_jsonl(self, destino: Path) -> Path:
        """Grava a trilha inteira de uma vez (arquivo temporário + troca): nunca pela metade."""
        with self._trava:
            if self.id_execucao is None:
                raise RuntimeError("chame iniciar_execucao antes de gravar a trilha")
            registros = [{"tipo_registro": "execucao", **self.execucao}] + [
                {"tipo_registro": "evento", "id_execucao": self.execucao["id_execucao"], **e}
                for e in self.eventos
            ]
        conteudo = "\n".join(_para_json(r) for r in registros) + "\n"
        destino.parent.mkdir(parents=True, exist_ok=True)
        descritor, provisorio = tempfile.mkstemp(dir=destino.parent, prefix=".auditoria-")
        try:
            with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
                arquivo.write(conteudo)
            os.replace(provisorio, destino)
        except BaseException:
            Path(provisorio).unlink(missing_ok=True)
            raise
        return destino

    # Ganchos de persistência: devolvem o que o destino decidiu (ex.: horários do servidor).
    # A versão em memória não grava nada fora do processo.
    def _persistir_inicio(self) -> dict[str, Any]:
        return {}

    def _persistir_evento(self, evento: dict[str, Any]) -> dict[str, Any]:
        return {}

    def _persistir_fim(self, encerramento: dict[str, Any]) -> dict[str, Any]:
        return {}


def _jsonb(valor: Any) -> Jsonb | None:
    return None if valor is None else Jsonb(valor, dumps=_para_json)


def _iso(momento: datetime) -> str:
    return momento.isoformat()


class AuditoriaPostgres(Auditoria):
    """Grava cada evento na hora (conexão em autocommit): uma queda não apaga a trilha.

    Use a conexão do papel `auditor`. Os horários (início, momento, fim) são os do servidor,
    devolvidos pelo banco, e o JSONL repete exatamente o que ficou gravado.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        if not conn.autocommit:
            raise ValueError(
                "AuditoriaPostgres exige conexão em autocommit: sem isso o evento fica numa "
                "transação aberta e some se o processo cair"
            )
        super().__init__()
        self._conn = conn

    def _persistir_inicio(self) -> dict[str, Any]:
        e = self.execucao
        (inicio,) = self._conn.execute(
            "INSERT INTO auditoria.execucoes "
            "(id_execucao, provedor, modelo, parametros, versao_codigo, hash_prompts) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING inicio",
            (self.id_execucao, e["provedor"], e["modelo"], _jsonb(e["parametros"]),
             e["versao_codigo"], e["hash_prompts"]),
        ).fetchone()  # fmt: skip
        return {"inicio": _iso(inicio)}

    def _persistir_evento(self, evento: dict[str, Any]) -> dict[str, Any]:
        (momento,) = self._conn.execute(
            "INSERT INTO auditoria.eventos (id_execucao, sequencia, no, tipo, ferramenta, "
            "argumentos, motivo, resultado, duracao_ms, tokens_entrada, tokens_saida, erro) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING momento",
            (self.id_execucao, evento["sequencia"], evento["no"], evento["tipo"],
             evento["ferramenta"], _jsonb(evento["argumentos"]), evento["motivo"],
             _jsonb(evento["resultado"]), evento["duracao_ms"], evento["tokens_entrada"],
             evento["tokens_saida"], evento["erro"]),
        ).fetchone()  # fmt: skip
        return {"momento": _iso(momento)}

    def _persistir_fim(self, encerramento: dict[str, Any]) -> dict[str, Any]:
        # O `fim` é o relógio do servidor (o gatilho do banco o impõe de qualquer forma).
        e = encerramento
        linha = self._conn.execute(
            "UPDATE auditoria.execucoes SET fim = now(), status = %s, data_referencia = %s, "
            "total_casos = %s, tokens_entrada = %s, tokens_saida = %s, custo_usd = %s "
            "WHERE id_execucao = %s RETURNING fim",
            (e["status"], e["data_referencia"], e["total_casos"], e["tokens_entrada"],
             e["tokens_saida"], e["custo_usd"], self.id_execucao),
        ).fetchone()  # fmt: skip
        if linha is None:
            raise LookupError(f"execução {self.id_execucao} não está em auditoria.execucoes")
        return {"fim": _iso(linha[0])}
