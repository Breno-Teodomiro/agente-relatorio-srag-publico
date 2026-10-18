"""Linha de comando: srag carga | indexar | qualidade | relatorio | grafo.

Códigos de saída: 0 deu certo; 1 falha ao rodar (ex.: banco fora do ar); 2 uso ou configuração
inválidos (argumento, .env incompleto, nenhuma chave de LLM). Mensagem de erro nunca repete
senha nem chave: o Config esconde o valor recebido e aqui só se mostra campo e mensagem.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import get_args

import psycopg
from pydantic import ValidationError

from srag_agente.config import RAIZ_PROJETO, Config
from srag_agente.ferramentas.registro import UF

ARQUIVO_QUALIDADE = RAIZ_PROJETO / "docs" / "QUALIDADE_DADOS.md"
ARQUIVO_GRAFO = RAIZ_PROJETO / "docs" / "grafo_langgraph.mmd"


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="srag", description="Agente de relatório de SRAG (PoC Indicium HealthCare)"
    )
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("carga", help="cria papéis e schemas e carrega raw → silver → gold (atômico)")
    sub.add_parser("indexar", help="indexa o dicionário de dados para o RAG")
    qualidade = sub.add_parser("qualidade", help="gera o relatório de qualidade dos dados")
    qualidade.add_argument("--saida", type=Path, default=ARQUIVO_QUALIDADE)
    grafo = sub.add_parser("grafo", help="gera o Mermaid do grafo a partir do código do agente")
    grafo.add_argument("--saida", type=Path, default=ARQUIVO_GRAFO)
    relatorio = sub.add_parser("relatorio", help="gera o relatório com o agente")
    relatorio.add_argument("--uf", default="BR", type=str.upper, choices=get_args(UF))
    relatorio.add_argument(
        "--pergunta", default=None, help="pergunta complementar (passa pelo guardrail de entrada)"
    )
    return parser.parse_args(argv)


def _erro(mensagem: str) -> None:
    print(mensagem, file=sys.stderr)


def _mensagem_configuracao(erro: ValidationError) -> str:
    campos = "\n".join(
        f"  - {'.'.join(map(str, e['loc']))}: {e['msg']}"
        for e in erro.errors(include_url=False, include_input=False, include_context=False)
    )
    return f"Configuração inválida (confira o .env; modelo em .env.example):\n{campos}"


def _grafo(destino: Path) -> int:
    from srag_agente.agente.grafo import estrutura_mermaid

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(estrutura_mermaid(), encoding="utf-8")
    print(f"Grafo salvo em {destino}")
    return 0


def _executar(args: argparse.Namespace, cfg: Config) -> int:
    from srag_agente import banco

    if args.comando == "carga":
        from srag_agente.dados import carga

        total = carga.executar_carga(cfg)  # raw + silver + gold numa transação só
        linhas = f"{total:,}".replace(",", ".")
        print(f"Carga concluída: {linhas} linhas na raw, silver e gold refeitas.")
    elif args.comando == "indexar":
        from srag_agente.rag.embeddings import criar_embedder
        from srag_agente.rag.indexar import campos_da_raw, indexar_dicionario

        with banco.conectar(cfg, "etl", autocommit=True) as conn:
            total = indexar_dicionario(conn, cfg.arquivo_dicionario,
                                       criar_embedder(cfg.pasta_cache_modelos),
                                       campos_da_raw(conn))  # fmt: skip
        print(f"Trechos do dicionário indexados: {total}")
    elif args.comando == "qualidade":
        from srag_agente.dados import qualidade

        with banco.conectar(cfg, "etl") as conn:
            print(f"Gerado {qualidade.gerar_relatorio_qualidade(conn, args.saida)}")
    elif args.comando == "relatorio":
        from srag_agente import nucleo

        resultado = nucleo.gerar_relatorio(cfg, uf=args.uf, pergunta_extra=args.pergunta)
        print(
            f"Situação: {resultado.status}\nHTML: {resultado.arquivos.html}\n"
            f"Markdown: {resultado.arquivos.markdown}\n"
            f"Auditoria: {resultado.arquivos.auditoria}\n"
            f"Custo estimado: US$ {resultado.custo_usd:.4f}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # uma linha por requisição é ruído

    if args.comando == "grafo":  # não precisa de banco nem de chave
        return _grafo(args.saida)

    from srag_agente.agente.llm import ConfiguracaoInvalida

    try:
        cfg = Config()
    except ValidationError as erro:  # só a do Config: a de outro ponto não é "configuração"
        _erro(_mensagem_configuracao(erro))
        return 2
    try:
        return _executar(args, cfg)
    except ConfiguracaoInvalida as erro:
        _erro(f"Configuração incompleta: {erro}")
        return 2
    except psycopg.OperationalError as erro:
        # a mensagem do psycopg traz host, porta e usuário; a senha nunca
        _erro(f"Não foi possível falar com o Postgres (o banco está de pé? `make subir`): {erro}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
