"""Linha de comando: srag carga | indexar | qualidade | relatorio | dicionario | grafo | demo.

Códigos de saída: 0 deu certo; 1 falha ao rodar (ex.: banco fora do ar); 2 uso ou configuração
inválidos (argumento, .env incompleto, teto fora da faixa, nenhuma chave de LLM); 130 Ctrl+C.
Mensagem de erro nunca repete senha nem chave: o Config esconde o valor recebido e aqui só se
mostra campo e mensagem, com dado pessoal mascarado; sem traceback no terminal.
"""

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import get_args

from pydantic import SecretStr, ValidationError

from srag_agente.config import RAIZ_PROJETO, Config
from srag_agente.ferramentas.registro import UF

ARQUIVO_QUALIDADE = RAIZ_PROJETO / "docs" / "QUALIDADE_DADOS.md"
ARQUIVO_GRAFO = RAIZ_PROJETO / "docs" / "grafo_langgraph.mmd"
PASTA_PACOTE = RAIZ_PROJETO / "demo" / "pacote"


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
    dicionario = sub.add_parser("dicionario", help="consulta o dicionário de dados (sem LLM)")
    dicionario.add_argument("pergunta", help="campo ou pergunta, ex.: 'o que é VACINA_COV?'")
    relatorio = sub.add_parser("relatorio", help="gera o relatório com o agente")
    relatorio.add_argument("--uf", default="BR", type=str.upper, choices=get_args(UF))
    relatorio.add_argument(
        "--pergunta", default=None, help="pergunta complementar (passa pelo guardrail de entrada)"
    )
    _argumentos_demo(sub)
    return parser.parse_args(argv)


def _argumentos_demo(sub: argparse._SubParsersAction) -> None:
    demo = sub.add_parser("demo", help="demonstração online: exportar, carregar e conferir")
    acoes = demo.add_subparsers(dest="acao", required=True)
    exportar = acoes.add_parser("exportar", help="gold local → pacote CSV + manifesto")
    exportar.add_argument("--saida", type=Path, default=PASTA_PACOTE)
    remotos = {
        "carregar": "carrega o pacote no banco remoto (numa transação só)",
        "conferir": "prova pelo efeito as permissões do banco remoto (não escreve nada)",
    }
    for nome, ajuda in remotos.items():
        acao = acoes.add_parser(nome, help=ajuda)
        acao.add_argument(
            "--env-arquivo",
            type=Path,
            required=True,
            help="configuração do banco remoto (ex.: .env.demo); nunca o .env",
        )
        acao.add_argument(
            "--alvo", required=True, help="host do banco remoto, digitado: igual ao PG_HOST"
        )
        if nome == "carregar":
            acao.add_argument("--pacote", type=Path, default=PASTA_PACOTE)
            # Texto sobre TLS é o padrão (o provedor recusa hash; a trava exige TLS).
            senha = acao.add_mutually_exclusive_group()
            senha.add_argument(
                "--senha-em-texto",
                dest="senha_em_texto",
                action="store_true",
                default=True,
                help="padrão: senha em texto sobre TLS",
            )
            senha.add_argument("--senha-em-hash", dest="senha_em_texto", action="store_false",
                               help="hash SCRAM no cliente (só se o provedor aceitar)")  # fmt: skip
            acao.add_argument("--trocar-senhas", action="store_true",
                              help="reenvia a senha de papel que já existe")  # fmt: skip
            acao.add_argument("--aceitar-tls-sem-verificacao", action="store_true",
                              help="senha em texto sem PG_SSLMODE=verify-full (risco)")  # fmt: skip


def _erro(mensagem: str) -> None:
    print(mensagem, file=sys.stderr)


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
    elif args.comando == "dicionario":
        from srag_agente.rag.consulta import abrir_busca, responder, validar_pergunta

        pergunta = validar_pergunta(args.pergunta)  # antes de abrir banco e carregar o modelo
        with abrir_busca(cfg) as busca:
            for resposta in responder(busca, pergunta):
                print(f"[{resposta.campo or '?'}] p.{resposta.pagina} ({resposta.origem})\n"
                      f"  {resposta.texto}\n")  # fmt: skip
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


# --- demo -------------------------------------------------------------------------------------
# Trecho do erro de conexão da libpq → dica. O texto original não é mostrado: traz host, IP e
# usuário do banco remoto.
_DICAS_CONEXAO = (
    ("password authentication failed", "senha ou usuário recusados (confira as senhas e "
                                       "PG_USUARIO_ADMIN no arquivo informado)"),
    ("certificate", "falha de TLS (confira PG_SSLMODE e PG_SSLROOTCERT)"),
    ("ssl", "falha de TLS (confira PG_SSLMODE e PG_SSLROOTCERT)"),
    ("could not translate host name", "host não encontrado (confira PG_HOST)"),
    ("name or service not known", "host não encontrado (confira PG_HOST)"),
    ("lock timeout", "outra sessão segura a tabela; tente de novo em instantes"),
    ("timeout", "tempo esgotado (o banco pode estar acordando; tente de novo)"),
    ("timed out", "tempo esgotado (o banco pode estar acordando; tente de novo)"),
    ("connection refused", "conexão recusada (confira PG_HOST e PG_PORTA)"),
    ("does not exist", "banco ou papel inexistente (confira PG_BANCO e PG_USUARIO_ADMIN)"),
)  # fmt: skip
_ENTRE_ASPAS = re.compile(r'"[^"]*"')


def _sem_segredo(texto: str, cfg: Config) -> str:
    """Última barreira: senha, chave, host e usuário do dono nunca saem no terminal."""
    for nome, valor in cfg:
        if isinstance(valor, SecretStr) and len(valor.get_secret_value()) >= 4:
            texto = texto.replace(valor.get_secret_value(), "***")
        elif nome in ("pg_host", "pg_usuario_admin") and valor:
            texto = texto.replace(valor, f"<{nome}>")
    return texto


def _mensagem_demo(erro: Exception) -> tuple[int, str]:
    import psycopg

    from srag_agente.agente.llm import ConfiguracaoInvalida
    from srag_agente.demo.carregar import CargaConfirmada, RecusaCarga
    from srag_agente.demo.pacote import PacoteInvalido
    from srag_agente.guardrails.padroes import mascarar_pii

    if isinstance(erro, CargaConfirmada):  # depois do COMMIT: a gold mudou, não dizer o contrário
        situacao = "foi interrompida" if erro.interrompida else f"falhou ({erro.causa})"
        dica = (" Se um papel já existia com outra senha, recarregue com --trocar-senhas."
                if erro.operacional else "")  # fmt: skip
        return (130 if erro.interrompida else 1), (
            f"Carga confirmada: a gold JÁ foi trocada (pacote {erro.sha256}). A conferência "
            f"depois do COMMIT {situacao}; rode `srag demo conferir --env-arquivo ... --alvo ...`."
            f"{dica}"
        )

    if isinstance(erro, RecusaCarga | PacoteInvalido):
        return 2, str(erro)
    if isinstance(erro, ConfiguracaoInvalida):
        return 2, f"Configuração incompleta: {erro}"
    if isinstance(erro, psycopg.OperationalError):
        texto = str(erro).lower()
        dica = next((d for trecho, d in _DICAS_CONEXAO if trecho in texto), None)
        return 1, f"Não foi possível usar o banco: {dica or 'falha de conexão'} " \
                  f"(SQLSTATE {erro.sqlstate or '-'})."  # fmt: skip
    if isinstance(erro, psycopg.Error):
        # Só a mensagem principal, sem DETAIL/CONTEXT (repetem linha e comando) e sem o que
        # vem entre aspas (valor, nome de usuário).
        principal = erro.diag.message_primary or str(erro).split("\n", 1)[0]
        principal = mascarar_pii(_ENTRE_ASPAS.sub('"…"', principal))[0][:300]
        return 1, f"Erro do Postgres (SQLSTATE {erro.sqlstate or '-'}): {principal}"
    return 1, f"Falha inesperada: {type(erro).__name__} (nada foi mostrado do texto do erro)."


def _imprimir_conferencias(conferencias) -> bool:
    for c in conferencias:
        situacao = "ok" if c.ok else "FALHOU"
        print(f"  [{situacao}] {c.papel}: {c.tentativa} → {c.obtido} (esperado: {c.esperado})")
    certas = sum(c.ok for c in conferencias)
    print(f"Conferências: {certas} de {len(conferencias)} ok")
    return certas == len(conferencias)


def _imprimir_avisos(avisos: list[str] | tuple[str, ...]) -> None:
    for aviso in avisos:
        print(f"  AVISO: {aviso}")


def _executar_demo(args: argparse.Namespace, cfg: Config) -> int:
    from srag_agente.demo import carregar as carga
    from srag_agente.demo import conferir, pacote

    if args.acao == "exportar":
        manifesto = pacote.exportar(cfg, args.saida)
        print(f"Pacote gravado em {args.saida}\n  sha256 do manifesto: {manifesto.sha256}")
        for tabela, dados in manifesto.tabelas.items():
            print(f"  {tabela}: {dados.linhas} linha(s)")
        avisos = pacote.avisos_pii(args.saida)
        if manifesto.tabelas["dicionario_trechos"].linhas == 0:
            avisos.append("dicionário vazio: rode `make indexar` antes de exportar")
        _imprimir_avisos(avisos)
        return 0
    if args.acao == "carregar":
        aceitar = args.aceitar_tls_sem_verificacao
        resumo = carga.carregar(cfg, args.pacote, alvo=args.alvo,
                                senha_em_texto=args.senha_em_texto,
                                trocar_senhas=args.trocar_senhas,
                                aceitar_tls_sem_verificacao=aceitar)  # fmt: skip
        print(f"Carga concluída em {args.alvo.strip()}\n  pacote (sha256): {resumo.sha256}\n"
              f"  data de referência: {resumo.data_referencia}")  # fmt: skip
        for tabela, linhas in resumo.linhas.items():
            print(f"  {tabela}: {linhas} linha(s)")
        print(f"  papéis criados: {', '.join(resumo.papeis_criados) or 'nenhum'}; "
              f"senha enviada para: {', '.join(resumo.senhas_enviadas) or 'nenhum'}")  # fmt: skip
        _imprimir_conferencias(resumo.conferencias)
        _imprimir_avisos(resumo.avisos)
        return 0 if resumo.ok else 1
    carga.conferir_alvo(cfg, args.alvo)  # conferir também só fala com o banco remoto digitado
    return 0 if _imprimir_conferencias(conferir.conferir_demo(cfg)) else 1


def _demo(args: argparse.Namespace) -> int:
    from srag_agente.demo import carregar as carga
    from srag_agente.erros import mensagem_configuracao

    try:
        if args.acao == "exportar":
            cfg = Config()  # exporta do banco local, com o .env local
        else:
            cfg = carga.ler_config_demo(carga.conferir_env_arquivo(args.env_arquivo))
    except ValidationError as erro:
        texto = mensagem_configuracao(erro)
        if args.acao != "exportar":
            texto = texto.replace(
                "confira o .env; modelo em .env.example",
                "confira o --env-arquivo; modelo em .env.demo.example",
            )
        _erro(texto)
        return 2
    except carga.RecusaCarga as recusa:
        _erro(str(recusa))
        return 2
    try:
        return _executar_demo(args, cfg)
    except KeyboardInterrupt:
        # Ctrl+C depois do COMMIT chega como CargaConfirmada; aqui é sempre antes dele.
        _erro("Interrompido pelo usuário (Ctrl+C) antes do COMMIT: a carga é uma transação só, "
              "nada mudou.")  # fmt: skip
        return 130
    except Exception as erro:
        codigo, mensagem = _mensagem_demo(erro)
        _erro(_sem_segredo(mensagem, cfg))
        return codigo


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # uma linha por requisição é ruído

    if args.comando == "grafo":  # não precisa de banco nem de chave
        return _grafo(args.saida)
    if args.comando == "demo":  # configuração e erros próprios (banco remoto)
        return _demo(args)

    from srag_agente.erros import classificar, mensagem_configuracao  # puxa o langchain

    try:
        cfg = Config()
    except ValidationError as erro:  # só a do Config: a de outro ponto não é "configuração"
        _erro(mensagem_configuracao(erro))
        return 2
    # Nenhum texto de erro vai cru ao terminal: `classificar` mascara e nunca mostra traceback.
    try:
        return _executar(args, cfg)
    except KeyboardInterrupt:
        _erro("Interrompido pelo usuário (Ctrl+C).")
        return 130
    except Exception as erro:
        codigo, mensagem = classificar(erro)
        _erro(mensagem)
        return codigo


if __name__ == "__main__":
    raise SystemExit(main())
