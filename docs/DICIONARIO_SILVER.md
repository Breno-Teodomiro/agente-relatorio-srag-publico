# Dicionário da camada silver

A `silver.casos` é a raw tipada e limpa, **só com o que alguma métrica usa e sem identificador**. Uma
linha por notificação de SRAG. Quem constrói é `src/srag_agente/dados/sql/01_silver.sql` (fonte da
verdade deste documento); a decisão de não levar colunas está no ADR
[0004](adr/0004-silver-sem-colunas-sensiveis.md).

- **Acesso:** só o papel `srag_etl`. O agente (`srag_agente`) lê apenas a `gold`, que são agregados.
- **Idempotente:** a tabela é recriada inteira a cada `make carga` (que carrega a `raw` e reconstrói a silver), a partir da `raw`.
- **Números de qualidade não ficam aqui.** Quantas linhas cada regra de limpeza afetou muda a cada
  carga; quem mede é `make qualidade`, que gera `docs/QUALIDADE_DADOS.md`.
- **Como a `raw` representa vazio:** como texto `''`, não `NULL`. Toda regra abaixo trata os dois.

## Colunas de `silver.casos`

Na ordem em que a tabela é criada. Os nomes de origem são os da `raw` (minúsculos, como o cabeçalho
do CSV). Os tipos vêm do `CREATE TABLE ... AS` (inteiro, texto ou data).

| Coluna | Tipo | Origem na raw | Regra de transformação | Significado em linguagem simples | Usada por |
|---|---|---|---|---|---|
| `id_caso` | `int` (chave primária) | gerada; no lugar de `nu_notific` | `row_number()` ordenado por `dt_sin_pri` e `nu_notific` | Número interno do caso. Não é o número da notificação e não aparece fora da silver | nenhuma view da gold (só contagem) |
| `uf` | `text`, pode ser nulo | `sg_uf_not` | Mantém só as 27 siglas válidas; qualquer outra coisa (inclusive vazio) vira `NULL`. Na gold, `NULL` aparece como `ND` | Estado onde o caso foi notificado | todas as views da gold (filtro por UF; `BR` = país inteiro) |
| `faixa_etaria` | `text` | `nu_idade_n` + `tp_idade` | Converte para anos completos (`tp_idade` 1 = dias ÷ 365, 2 = meses ÷ 12, 3 = anos; idade em anos acima de 120 ou valor não numérico → ignorado) e agrupa em `<1`, `1-4`, `5-11`, `12-17`, `18-39`, `40-59`, `60+` ou `ignorado` | Faixa de idade do paciente. A idade exata não passa da raw | `gold.distribuicao` |
| `dt_notificacao` | `date`, pode ser nulo | `dt_notific` | Função `silver.para_data` com a janela de datas aceitas (ver abaixo) | Dia em que o caso foi notificado | nenhuma view da gold (a janela de datas aceitas usa a coluna `dt_notific` da raw, não esta) |
| `dt_sintomas` | `date`, pode ser nulo | `dt_sin_pri` | idem | Dia do primeiro sintoma. É a data de referência das métricas | `gold.referencia`, `casos_diarios`, `indicadores`, `janelas_aumento` |
| `dt_internacao` | `date`, pode ser nulo | `dt_interna` | idem; aceita também o formato `AAAA-MM-DD hh:mm:ss.nnnnnnnnn` | Dia da internação | nenhuma view da gold |
| `dt_entrada_uti` | `date`, pode ser nulo | `dt_entuti` | idem | Dia da entrada na UTI | nenhuma view da gold |
| `dt_evolucao` | `date`, pode ser nulo | `dt_evoluca` | idem | Dia da alta ou do óbito | nenhuma view da gold |
| `hospitalizado` | `text` | `hospital` | `silver.sim_nao`: `1` → `sim`, `2` → `nao`, qualquer outro (incluindo `9` e vazio) → `ignorado` | O paciente foi internado? | nenhuma view da gold |
| `uti` | `text` | `uti` | `silver.sim_nao` (mesma tabela acima) | O paciente passou por UTI? | métrica de UTI (`gold.indicadores`) |
| `suporte_ventilatorio` | `text` | `suport_ven` | `1` → `invasivo`, `2` → `nao_invasivo`, `3` → `nao`, qualquer outro → `ignorado` | Precisou de ventilação mecânica? De que tipo? | nenhuma view da gold |
| `evolucao` | `text` | `evolucao` | vazio → `sem_desfecho`; `1` → `cura`; `2` → `obito`; `3` → `obito_outras_causas`; qualquer outro → `ignorado` | Desfecho do caso. `sem_desfecho` é caso ainda sem resposta, diferente de `ignorado` | métrica de mortalidade (`gold.indicadores`) |
| `classificacao` | `text` | `classi_fin` | vazio → `em_investigacao`; `1` → `influenza`; `2` → `outro_virus`; `3` → `outro_agente`; `4` → `nao_especificado`; `5` → `covid19`; qualquer outro → `ignorado` | Causa final do caso, depois da investigação | `gold.distribuicao` |
| `vacina_gripe` | `text` | `vacina` | `silver.sim_nao` | Recebeu vacina contra gripe na última campanha? | métrica de vacinação (`gold.indicadores`) |
| `vacina_covid` | `text` | `vacina_cov` | `silver.sim_nao` | Recebeu vacina contra COVID-19? | métrica de vacinação (`gold.indicadores`) |

Índices: chave primária em `id_caso`, mais índices em `dt_sintomas` e em `uf`.

A coluna "Usada por" foi conferida lendo `03_gold.sql`: as colunas marcadas "nenhuma view da gold"
ficam na silver por estarem entre os campos de desfecho e cronologia previstos no desenho, mas
nenhuma métrica atual as consulta (ver o limite de minimização no ADR 0004).

### Datas: a janela de datas aceitas

A raw traz datas impossíveis (ex.: anos 1695 e 2202) e dois formatos de texto. `silver.para_data`
resolve assim:

1. **Formato:** lê os 10 primeiros caracteres `AAAA-MM-DD`. Serve tanto para `2025-03-14` quanto para
   `2025-03-14 00:00:00.000000000` (o formato com nanossegundos que aparece em `dt_interna`). Qualquer
   texto fora desse padrão vira `NULL`.
2. **Dia que não existe** (ex.: 30 de fevereiro) vira `NULL` em vez de derrubar a carga.
3. **Fora da janela** vira `NULL`. A janela é calculada pela própria base, na tabela
   `silver.limites`, em dois passos:
   - só contam as `dt_notific` válidas a até 365 dias da **mediana** delas (as "plausíveis");
   - janela = `[menor plausível − 365 dias, maior plausível]`.

   O primeiro passo existe porque uma única notificação de 1695 ou 2202 alargaria a janela de todas as
   datas e deslocaria o "hoje" (máximo de `dt_sintomas`).
4. **Falha fechada:** sem nenhuma `dt_notific` válida, a janela fica vazia e todas as datas viram `NULL`.

A data inválida não apaga o caso: o caso continua na silver, só com aquela data nula. Quantos casos e
quais datas foram afetados: `make qualidade`.

### Domínios de código (`silver.sim_nao` e afins)

A raw usa códigos numéricos do DATASUS (`1` = sim, `2` = não, `9` = ignorado). A silver troca por texto
legível. Código fora do domínio não derruba a carga: vira `ignorado` (ou, em `evolucao` e
`classificacao`, o valor vazio tem rótulo próprio, descrito na tabela). A contagem de códigos fora do
domínio também sai em `make qualidade`.

## Colunas que NÃO passam da raw

A `raw` guarda o CSV como veio (só `srag_etl` enxerga). A passagem raw → silver **não leva** as colunas
abaixo. O critério é minimização (LGPD art. 6º, III): dado de saúde, raça e etnia é dado pessoal
sensível (art. 5º, II), e nascimento + município + sexo + datas permitem reidentificar uma pessoa em
base pública. Não ter o dado na camada é mais forte do que escondê-lo por permissão (ADR 0004).

| Coluna(s) da raw | Motivo da exclusão | Categoria |
|---|---|---|
| `nu_notific` (número da notificação) | identifica o registro na origem; vira a chave interna `id_caso` | reidentificação |
| `dt_nasc` (data de nascimento) | junto com município e sexo reidentifica; a idade vira só faixa etária | reidentificação |
| municípios de notificação, residência e internação, e nome e código da unidade de internação (ex.: `nm_un_inte`) | localização fina é quase-identificador; a menor unidade geográfica que fica é a UF | reidentificação |
| `cs_raca`, `cs_etinia` (raça/cor e etnia) | dado sensível (LGPD art. 5º, II); nenhuma métrica usa | sensível |
| `cs_sexo` | quase-identificador; nenhuma métrica usa | reidentificação / sem uso |
| `cs_escol_n` (escolaridade) | quase-identificador; nenhuma métrica usa | reidentificação / sem uso |
| `pac_cocbo`, `pac_dscbo` (ocupação) | quase-identificador; nenhuma métrica usa | reidentificação / sem uso |
| lotes de vacina | rastreia a dose de uma pessoa; só se guarda "vacinou ou não" | reidentificação |
| `tem_cpf`, `pov_ct`, `estrang` | indicam situação pessoal (documento, população tradicional, nacionalidade); nenhuma métrica usa | sensível / sem uso |
| campos de texto livre (os terminados em `_des` e `_out`, e `morb_desc`) | texto digitado à mão pode trazer nome, endereço ou história clínica; não dá para garantir que esteja limpo | reidentificação |
| sintomas e demais campos clínicos não listados na tabela da silver | nenhuma métrica usa | sem uso |
| idade exata (`nu_idade_n`, `tp_idade`) | só entra transformada em `faixa_etaria` | minimização |

Consequência: o agente não consegue citar município, sexo, raça ou idade exata nem por engano, porque
o dado não existe na silver nem na gold. Uma análise por município ou sexo exigiria um novo ADR e uma
nova coluna.

**Limite conhecido:** `id_caso` é um pseudônimo reversível por quem tem acesso à raw (é a posição do
caso ordenado por `dt_sin_pri` e `nu_notific`). A proteção é o papel do banco (`REVOKE` no schema
`silver`, e a gold só tem agregados), não o número em si.

## Como conferir

| Pergunta | Comando |
|---|---|
| Quantas linhas cada regra de limpeza alterou | `make qualidade` (gera `docs/QUALIDADE_DADOS.md`) |
| Colunas e tipos reais da silver no banco | `docker compose exec banco psql -U postgres -d srag -c '\d silver.casos'` |
| Janela de datas aceitas nesta carga | `SELECT * FROM silver.limites;` com o mesmo `psql` |
| Reconstruir a silver | `make carga` |
