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

- **Marca no relatório:** a lista de domínios confiáveis só ordena, não filtra. Por isso a
  seção Referências (Markdown e HTML) marca "fonte não verificada" quando `confiavel` é falso e
  "sem data" quando a notícia não traz data, e a seção de governança explica as duas marcas. A
  marca informa o leitor; não bloqueia a citação.

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

- Os testes automatizados não usam a rede; a autenticação `Bearer` foi conferida contra a API
  real numa execução de ponta a ponta (a trilha de auditoria registra `provedor: tavily`
  nas buscas). Mudança futura no formato da API só aparece nessa execução, não na suíte.
- O teto é de 4 chamadas a `buscar_noticias` por execução (uma por janela basta; o excedente é
  recusado com mensagem ao orquestrador). O filtro de relevância exige o tema (síndrome
  respiratória ou SRAG) e um termo de saúde pública e barra uma lista pequena e declarada de
  domínios (redes sociais e sites financeiros, `DOMINIOS_BLOQUEADOS` em `noticias.py`). Passa
  notícia estrangeira que fale de SRAG e casos, e sigla de outro assunto com termo de saúde em
  domínio fora da lista.
- A trilha trunca o resultado de cada busca em 2000 caracteres (`LIMITE_RESULTADO`): título e
  trecho das notícias **não citadas** podem ficar só em parte. As notícias **citadas** pelo texto
  ficam registradas à parte, em `argumentos.noticias_citadas` do evento `guardrail_saida` (id da
  trilha, URL exata, data e janela; o título não entra), e `resultado.total_noticias_citadas`
  guarda a contagem. Um número que vem só de uma notícia é auditável pela URL citada, não pelo
  trecho. Essa lista tem o mesmo teto de 2000 caracteres por campo: com muitas citações de URL
  longa ela sai truncada (`resumo_truncado` + `tamanho_original`), e a contagem continua
  inteira. O teste `test_trilha_guarda_as_noticias_citadas_mesmo_com_a_busca_truncada` cobre
  sete citações com a busca truncada. O relatório renumera as referências na ordem de leitura;
  a URL liga as duas numerações.
- A relevância das notícias depende do filtro local do buscador: a Tavily devolve resultados
  fora de assunto (por exemplo, a sigla SRAG de outras áreas) e o filtro descarta o que não
  trata de saúde pública.
- O timeout é por leitura, não do tempo total do download.
- O Google News RSS não é API documentada; pode mudar sem aviso.
- Item sem data é aceito em qualquer janela (ADR 0007).
- A detecção de injeção é por padrões (limites no ADR 0005).
