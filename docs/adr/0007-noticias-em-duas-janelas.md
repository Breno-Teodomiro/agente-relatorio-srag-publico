# ADR 0007 — Notícias em duas janelas: período dos dados × cenário atual

- **Status:** aceita (14/10/2026, spec §3.3)

## Contexto

O desafio pede comentários com notícias "em tempo real". A base termina na data de referência
(jun/2025), e a execução acontece meses depois. Notícia de hoje não explica número de junho de
2025; misturar as duas coisas produziria causalidade falsa.

## Decisão

- `buscar_noticias` recebe `janela ∈ {periodo_dos_dados, cenario_atual}`
  (`ferramentas/registro.py`):
  - **período dos dados:** 30 dias até a data de referência da base. Embasa os números.
  - **cenário atual:** 30 dias até a data da execução. Atende ao "tempo real".
- O filtro de data é aplicado de novo no código depois da busca (o provedor pode devolver item
  fora do intervalo).
- `verificar_cobertura` exige uma busca em cada janela; busca sem resultado conta, e o relatório
  diz que não houve notícia, sem inventar.
- O guardrail de saída barra notícia de uma janela citada na seção da outra e notícia com data
  fora da busca que a trouxe.

## Alternativas consideradas

- **Só notícias atuais:** descartada; não embasam os números da base.
- **Só notícias do período:** descartada; não atende ao "tempo real".
- **Uma busca única sem separar:** descartada; o LLM tenderia a usar notícia atual para explicar
  dado antigo, e o guardrail não teria como conferir.

## Consequências

- O relatório tem duas seções de notícias com rótulo claro; a regra "notícia atual nunca explica
  número do período" é conferida por código, não só pedida no prompt.

## Limites conhecidos

- Item **sem data** é aceito em qualquer janela (a fonte nem sempre informa); a citação dele gera
  aviso, não bloqueio.
- O "tempo real" depende do relógio da máquina que executa (`date.today()`).
