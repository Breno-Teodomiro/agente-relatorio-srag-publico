# ADR 0009 — Notícias por `httpx` direto (Tavily → Google News RSS), conteúdo externo como dado

- **Status:** aceita (Task 7; registrada em 17/10/2026). Substitui a menção a `langchain-tavily`
  na tabela de stack do spec §8.

## Contexto

O spec previa `langchain-tavily` com reserva em RSS. Na implementação, a busca precisava de
controles que o wrapper não expõe de forma simples: limite de bytes da resposta, timeout por
leitura, erro sem a chave da API, decisão de reserva **depois** dos filtros e teste sem rede.

## Decisão

- `ferramentas/noticias.py` chama a API da Tavily com `httpx` (POST `/search`, `topic=news`,
  `start_date`/`end_date`, autenticação `Authorization: Bearer`). Sem chave no `.env`, vai
  direto ao RSS.
- **Reserva:** Google News RSS (`feedparser`), com `after:`/`before:` na consulta e o filtro de
  data repetido no código. O RSS entra quando a Tavily falha (erro HTTP, resposta inválida ou
  grande demais) **e também** quando responde mas nenhuma notícia sobra depois dos filtros.
- Download em streaming com teto de bytes e timeout; resposta malformada vira texto em `erros`,
  com a chave trocada por `***`. O resultado diz qual provedor respondeu.
- **Conteúdo externo é dado, nunca instrução** (`guardrails/conteudo_externo.py`): item com
  padrão de injeção no título, trecho ou fonte é descartado e contado
  (`descartadas_por_injecao`); URL só `http/https`; o texto é limpo (entidades desfeitas, NFKC,
  tags removidas, `<`/`>` neutralizados) e cortado (título 200, trecho 500 caracteres); fontes
  de uma lista de domínios confiáveis vêm primeiro. Ao LLM, cada notícia vai dentro de
  `<noticia_externa>`, com os campos como string JSON e o aviso de que é dado não confiável.

## Alternativas consideradas

- **`langchain-tavily`:** descartada; uma dependência a mais, sem controle fino de bytes,
  timeout e mensagem de erro, e mais difícil de testar sem rede.
- **Só RSS:** descartada como principal; menos controle de data e de relevância. Fica como
  reserva sem chave.
- **Ferramenta de busca do LangChain exposta direto ao LLM:** descartada; a ferramenta do agente
  é fechada (ADR 0001) e o resultado passa pela sanitização antes de qualquer prompt.

## Consequências

- Testável sem rede (dublês de `httpx` e um RSS de exemplo em `tests/dados/`); a falha das duas
  fontes produz relatório que diz que não houve notícia.
- Mudança de contrato da API da Tavily é nossa para acompanhar (o wrapper faria isso por nós).

## Limites conhecidos

- O formato da autenticação `Bearer` não foi conferido contra a API real durante a
  implementação (sem rede nos testes); conferir com uma chamada real e a chave do Breno na
  verificação final (Task 19).
- O timeout é por leitura, não do tempo total do download.
- O Google News RSS não é API documentada; pode mudar sem aviso.
- Item sem data é aceito em qualquer janela (ADR 0007).
- A detecção de injeção é por padrões (limites no ADR 0005).
