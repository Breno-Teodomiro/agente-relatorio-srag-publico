# ADR 0005 — Guardrails próprios, determinísticos, em camadas; relatório HTML com CSP por hash

- **Status:** aceita (14/10/2026, spec §6; CSP acrescentada na Task 14)

## Contexto

O agente lê texto que não controlamos (pergunta do usuário, notícias, PDF do dicionário) e
publica texto escrito por um LLM. Os riscos: injeção direta e indireta, número inventado, link
inventado, dado pessoal, recomendação clínica, laço e custo sem teto, e script no HTML final.

## Decisão

Código próprio, determinístico sempre que possível; LLM só onde regra não resolve (o
classificador de escopo da pergunta). Três momentos, que cobrem as cinco camadas do spec §6:

1. **Entrada** — `guardrails/entrada.py`: tamanho (500 car.), normalização Unicode (NFKC, sem
   caracteres invisíveis), padrões de injeção (`guardrails/padroes.py`), dado pessoal, e só no
   fim o classificador LLM `{no_escopo, motivo}`. Falha do classificador **recusa** (falha
   fechada); o relatório sai sem a pergunta extra. Conteúdo externo que entra pelas ferramentas
   (notícias, trechos do PDF) é limpo e passado como **dado** (`guardrails/conteudo_externo.py`,
   ADR 0009); parâmetros de ferramenta são validados por Pydantic (ADR 0001).
2. **Saída** — `guardrails/saida.py`, antes de publicar: mascara dado pessoal; confere cada
   número contra as evidências com tolerância só de arredondamento; sentido de variação; links
   que não estão nos resultados; notícia de uma janela usada na outra; contagem de 1 a 4 (que a
   supressão nunca exibe, ADR 0011); recomendação clínica; HTML e instrução a modelo. Violação
   → até 2 novas redações, cada uma com uma **instrução de correção** só para o LLM (o valor
   exato do campo do JSON, a notícia a citar na mesma frase, as datas permitidas ou o que
   remover; só valores das evidências) → faixa "revisão humana necessária" (status
   `revisao_humana`). A instrução não muda o que barra e não vai à trilha; o texto
   reprovado é retido (`agente/nos.py`): o relatório sai com o que vem do SQL, a faixa com as
   violações e um aviso de retenção no lugar dos comentários; a trilha guarda as violações e o
   tamanho e o SHA-256 do texto retido, nunca o texto.
3. **Execução** — `guardrails/limites.py`: 12 chamadas de ferramenta, 60.000 tokens, 300 s,
   2 voltas de cobertura, 3 tentativas de redação. Reserva OpenRouter → OpenAI
   (`agente/llm.py`, `with_fallbacks`) e Tavily → RSS (ADR 0009). Teto atingido → para,
   registra e entrega relatório parcial com aviso.

**Relatório:** HTML autocontido (gráficos em SVG embutido, nada carregado de fora), Jinja2 com
autoescape, Markdown com escape próprio (`relatorio/markdown.py`), links só `http/https`. A
política de conteúdo (`relatorio/montar.py`) é `default-src 'none'` e `script-src` com **só o
hash SHA-256** do único script da página (alternador de tema): texto que escape do escape e vire
`<script>` não executa. Aviso fixo de prova de conceito inserido pelo código.

## Alternativas consideradas

- **NeMo Guardrails / Guardrails AI:** descartadas; dependência pesada, regra menos explicável
  e mais difícil de testar caso a caso.
- **LLM como juiz da saída:** descartado como barreira principal; não é determinístico e custa
  uma chamada a mais. Regra conferível vem primeiro.
- **CSP com `'unsafe-inline'` em script ou com nonce:** descartadas; o relatório é arquivo
  estático, então hash fixo é mais simples e mais restrito que nonce.

## Consequências

- Cada regra tem teste de ataque em `tests/guardrails/` (e o README mostra a tabela ataque →
  resultado). Falso positivo é aceito onde o custo é pequeno (notícia legítima descartada).
- O relatório nunca publica texto reprovado: sai marcado para revisão humana, com o texto
  retido (a violação listada na faixa repete só um trecho curto e mascarado do que foi barrado).
- Markdown: endereço em texto livre tem o autolink quebrado (`https\://`, `www\.`), para o
  visualizador GFM não o tornar clicável.

## Limites conhecidos (declarados nas docstrings e testados como `xfail` quando cabe)

- Padrões de injeção não pegam homóglifos de outro alfabeto, conteúdo codificado (base64) nem
  paráfrase nova.
- A conferência numérica prova que o número **existe** nas evidências, não a que se refere.
- Número por extenso só é procurado junto de substantivo de contagem e quando há supressão.
- Lista de termos clínicos não cobre paráfrase.
- A CSP permite `style-src 'unsafe-inline'` (estilo embutido, sem script).

## Como conferir

`tests/guardrails/` e `tests/unit/test_relatorio.py` (escape, CSP, links); `make testes`.
