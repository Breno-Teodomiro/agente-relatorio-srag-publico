"""Conferência do texto do LLM antes de publicar (camada 4 do spec §6).

Primeiro mascara dados pessoais; o texto publicado é o mascarado e sem caracteres invisíveis. As
conferências rodam sobre a forma normalizada (NFKC, sem ``Cf``): largura total e largura zero
não escondem número, link nem instrução.

Barra (violação → nova redação → revisão humana):

- número que não está nas evidências, com tolerância só de arredondamento na casa citada
  ("10%" e "10,0%" passam para 0,1; "9,9%" e "30%" para 0,322 não). Percentual só confere com
  campo de fração (``valor``, ``percentual``…), contagem só com contagem: 84 casos não viram 84%;
- variação com o sentido trocado ("caíram 20%" quando a variação foi de +20%);
- número de notícia fora da frase que cita a notícia ([n]);
- contagem de 1 a 4 de casos, óbitos etc. (a supressão nunca as exibe) e afirmação "<5" sem
  célula "<5" nas evidências; "<5" e "oculto" vindos dos dados são rótulos, não números, e nada é
  posto no lugar deles. Quando há "<5" ou "oculto" nas evidências, também a contagem por extenso
  ("três casos", "dezessete óbitos");
- proporção "N em cada M" e fração em palavras sobre contagem ("um terço dos casos", "metade dos
  internados") que, como percentual inteiro, não conferem com as evidências; variação em palavras
  ("dobraram", "triplicou", "caiu pela metade") que não confere com uma variação das evidências;
- número por extenso: percentual ("vinte e oito por cento") e contagem ("cinquenta casos") sempre
  barram, porque não há casa decimal a conferir; de 2 a 4 ("três casos") é valor suprimido;
- número derivado (diferença, soma, razão, "pontos") só passa se coincidir com um número das
  evidências: a tolerância é só a de arredondamento na casa exibida;
- ano e data que não aparecem nas evidências. Inteiro de 1900 a 2100 sem ponto é ano, salvo em
  posição de quantidade, quando é contagem e confere só com contagem: (a) substantivo de contagem
  colado depois dele ou depois de até 2 adjetivos de uma lista curta ("2025 óbitos", "2025 novos
  e graves casos"); (b) rótulo de contagem antes ("Óbitos: 2025") ou verbo de medida antes, numa
  oração que não fala de "ano(s)" ("foram 2025", "somaram 1938"; no singular ou com "de", só se
  a oração fala de contagem ou total: "o total de óbitos foi 2025"). Artigo, preposição, verbo,
  outro número ou [n] entre o ano e a contagem deixam o ano ser ano ("Em 2025 os casos",
  "Em 2025 84 casos", "SE 23 de 2025");
- citação inexistente, notícia de uma janela usada em seção da outra, notícia com data fora da
  busca que a trouxe; link que não é exatamente uma das URLs devolvidas (com ou sem esquema);
- recomendação clínica, marcação HTML e instrução dirigida a modelo.

Avisa (não barra): citação de notícia sem data, porque a busca aceita item sem data em qualquer
janela.

Instrução de correção: cada violação de número, data ou fração ganha uma instrução acionável para
a nova redação ("uti.valor é 29,4%: escreva exatamente 29,4%"; "cite [5] na MESMA frase ou remova
o número"; as datas e as variações que o JSON traz). Ela só usa valores das evidências, cada um
com o campo de onde veio, e vai SÓ ao LLM da nova tentativa: a violação, que vai à trilha e ao
relatório, continua curta. A instrução não muda o que barra.

Não são números a conferir: ordinais ("1º semestre", "2ª onda"), as janelas da metodologia só
quando seguidas da unidade (7, 12, 14 e 30 dias/semanas/meses; 1 e 2 semanas), "4 métricas",
"2 gráficos", "27 UFs", "por 100 mil habitantes", semana epidemiológica de 1 a 53 e os rótulos
de vírus (COVID-19, SARS-CoV-2, H1N1). Contagem pequena (1 a 4) sem substantivo de contagem
("as 3 fontes") é conferida como qualquer número, não como valor suprimido.

Limitações declaradas: confere que o número EXISTE nas evidências, não a que ele se refere ("80
óbitos" passaria escrito como "80 internações em UTI"; o 28,6% do apoio de 30 dias passaria
descrito como "nos últimos 30 dias", que é outra janela); contagem por extenso só é procurada
junto de substantivo de contagem ("três em RR", "dezenas de casos" e "meia dúzia" passam), e
"um caso" só conta quando há supressão nos dados; fração em palavras só junto de "do/da/dos/das" +
contagem; domínio sem esquema nem ``www`` só é reconhecido nas terminações da lista ``_TLDS``; a
lista de termos clínicos não cobre paráfrase. Ano x contagem é decidido pela posição, não pelo
sentido: ano legítimo colado à contagem sem vírgula ("Em 2025 casos foram...", "Em 2025 novos
casos...") e ano depois de verbo de medida no plural numa oração sem "ano" ("Os destaques foram
2024 e 2025") barram, e a instrução pede a vírgula; contagem de 4 algarismos fora dessas posições
e sem ponto de milhar ("1938 internaram-se", "somaram ao todo 1938") é lida como ano e só passa se
o ano estiver nas evidências. O prompt pede ano como data ("em 2025,") e milhar com ponto.
"""

import contextlib
import math
import re
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel

from srag_agente.ferramentas.registro import Evidencia
from srag_agente.guardrails.padroes import (
    TRACOS,
    contem_injecao,
    mascarar_pii,
    normalizar_unicode,
)
from srag_agente.metricas.supressao import LIMIAR_SUPRESSAO, ROTULO_OCULTO, ROTULO_SUPRIMIDO
from srag_agente.modelos import CITACAO, NoticiaCitavel, TextoRelatorio

LIMITE_VIOLACOES = 50
_LIMITE_TRECHO = 80
_EPSILON = 1e-9

_MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}  # fmt: skip
_MES = "(" + "|".join(_MESES) + ")"
# Traços tipográficos e o sinal de menos viram "-" (a mesma tabela do mascaramento); as aspas
# angulares simples (U+2039/U+203A, usadas na neutralização de tags) voltam a ser "<" e ">".
_TRACOS = {**TRACOS, 0x2039: "<", 0x203A: ">"}

# ---- padrões de texto --------------------------------------------------------------------------
_PARA_URL = r"[^\s<>\"'\]\)]"
_URL_ESQUEMA = re.compile(rf"\b[a-z][a-z0-9+.-]*://{_PARA_URL}*", re.IGNORECASE)
_ESQUEMA_PERIGOSO = re.compile(r"\b(?:javascript|data|vbscript|file):", re.IGNORECASE)
_WWW = re.compile(rf"(?<![\w./-])www\.{_PARA_URL}+", re.IGNORECASE)
_TLDS = (
    "com|net|org|br|gov|edu|int|info|io|co|app|dev|xyz|site|online|news|ru|cn|tk|ly|link|click"
    "|top|biz|uk|pt|fr|ar|mx|tv|cc|ai|ws|live|shop|blog|website|space|tech|zip"
)
_DOMINIO = re.compile(
    rf"(?<![\w@./-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:{_TLDS})(?![\w-])(?:/{_PARA_URL}*)?",
    re.IGNORECASE,
)
_PONTUACAO_FINAL = ".,;:!?"
_DATA_EXTENSO = re.compile(
    rf"\b(\d{{1,2}})(?:o|°)?\s+de\s+{_MES}(?:\s+de\s+(\d{{4}}))?\b", re.IGNORECASE
)
_MES_ANO_EXTENSO = re.compile(rf"\b{_MES}\s+(?:de\s+)?(\d{{4}})\b", re.IGNORECASE)
_DATA_ISO = re.compile(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b")
_DATA_BARRA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")
_MES_ANO_BARRA = re.compile(r"\b(\d{1,2})/(\d{4})\b")
_DIA_MES_BARRA = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
# Número seguido de percentual ou de milhar é número, nunca rótulo: "1 a 4%" e "menos de 5 mil".
_NAO_E_ROTULO = r"(?![.,]\d)(?!\s*(?:%|por\s+cento\b|mil\b|milh|bilh))"
# Só prefixos conhecidos: "subiu-50%" ou "alta-90%" não são rótulo e o número tem de ser conferido.
_ROTULOS = re.compile(
    r"\bCoV-\d+\b|(?<![\w.,])(?-i:SE)-\d{1,2}\b|\bcovid[\s-]?19\b|\bh\d+n\d+\b", re.IGNORECASE
)
_REGRA_SUPRESSAO = re.compile(rf"\b1\s*(?:a|-|até)\s*4\b(?!\s*anos?){_NAO_E_ROTULO}",
                              re.IGNORECASE)  # fmt: skip
_ROTULO_MENOS_DE_5 = re.compile(
    rf"(?:<|\bmenos\s+de|\binferior\s+a|\babaixo\s+de)\s*5(?!\d){_NAO_E_ROTULO}", re.IGNORECASE
)
_NUM_SOLTO = r"(?<![\w.,])"
# Números que descrevem o próprio relatório ou a metodologia, e não um dado: só com a unidade.
_CONTEXTOS_FIXOS = re.compile(
    rf"{_NUM_SOLTO}(?:7|12|14|30)\s+(?:dias|semanas|meses)\b"
    rf"|{_NUM_SOLTO}[12]\s+semanas?\b"
    rf"|{_NUM_SOLTO}4\s+(?:m[ée]tricas|indicadores)\b"
    rf"|{_NUM_SOLTO}2\s+gr[áa]ficos\b"
    rf"|{_NUM_SOLTO}27\s+(?:UFs?\b|unidades\s+(?:da\s+federa[çc][ãa]o|federativas)\b)"
    r"|\bpor\s+(?:cada\s+)?(?:100|cem)\s+mil(?:\s+habitantes)?\b"
    r"|\bpor\s+100\.000(?:\s+habitantes)?\b"
    r"|(?:\bsemana\s+epidemiol[óo]gica|(?<![\w-])(?-i:SE))\s+(?:[1-9]|[1-4]\d|5[0-3])(?!\w|[.,]\d)"
    r"(?!\s*%)",
    re.IGNORECASE,
)
_PROPORCAO = re.compile(
    rf"{_NUM_SOLTO}(\d{{1,3}})\s+(?:em|a|de)\s+cada\s+(\d{{1,4}})(?![\w.,]?\d)", re.IGNORECASE
)
_SUBSTANTIVO_CONTAGEM = (
    r"(?:casos?|[óo]bitos?|mortes?|mortos?|interna[çc](?:[ãa]o|[õo]es)|internad[oa]s?"
    r"|hospitaliza[çc](?:[ãa]o|[õo]es)|hospitalizad[oa]s?|pacientes?|notifica[çc](?:[ãa]o|[õo]es)"
    r"|pessoas?|registros?|v[íi]timas?|crian[çc]as?|idos[oa]s?|gestantes?|doentes?"
    r"|infectad[oa]s?|vacinad[oa]s?)"
)
# Depois do número: substantivo de contagem, com no máximo uma palavra no meio ("2 novos casos").
_SEGUE_CONTAGEM = re.compile(rf"\s+(?:[\w-]+\s+)?{_SUBSTANTIVO_CONTAGEM}\b", re.IGNORECASE)
# Inteiro com cara de ano em posição de quantidade (ver `_ano_como_contagem`). (a) Depois dele:
# o substantivo colado ou depois de até 2 adjetivos desta lista curta ("2025 novos e graves
# casos"); artigo, preposição, verbo, número e [n] no meio deixam o ano ser ano.
_ADJETIVO_CONTAGEM = (
    r"(?:nov[oa]s|mais|graves|outr[oa]s|confirmad[oa]s|notificad[oa]s|registrad[oa]s|suspeitos)"
)
_CONTAGEM_DEPOIS = re.compile(
    rf"\s*(?:{_ADJETIVO_CONTAGEM}(?:\s*,\s*|\s+e\s+|\s+)){{0,2}}{_SUBSTANTIVO_CONTAGEM}\b",
    re.IGNORECASE,
)
# (b) Antes dele, na mesma oração: rótulo de contagem ("Óbitos: 2025") ou verbo de medida.
_ROTULO_CONTAGEM = re.compile(rf"\b(?:{_SUBSTANTIVO_CONTAGEM}|total)\s*:\s*$", re.IGNORECASE)
_VERBO_MEDIDA = re.compile(
    r"\b(?:(?P<plural>foram|somaram|totalizaram|atingiram|chegaram\s+a)"
    r"|foi|somou|totalizou|atingiu|chegou\s+a)"
    r"\s+(?P<de>de\s+)?(?:cerca\s+de\s+|aproximadamente\s+|quase\s+)?$",
    re.IGNORECASE,
)
_FALA_DE_CONTAGEM = re.compile(rf"\b(?:{_SUBSTANTIVO_CONTAGEM}|total)\b", re.IGNORECASE)
_FALA_DE_ANO = re.compile(r"\banos?\b", re.IGNORECASE)
_FIM_DE_ORACAO = re.compile(r"[.;!?,]\s|[()]")
_EXTENSO_PEQUENO = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3, "quatro": 4}
_PALAVRA_NUMERO = (
    r"(?:um|uma|dois|duas|tr[êe]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze"
    r"|quatorze|catorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte|trinta|quarenta"
    r"|cinquenta|sessenta|setenta|oitenta|noventa|cem|cento|duzent[oa]s|trezent[oa]s"
    r"|quatrocent[oa]s|quinhent[oa]s|seiscent[oa]s|setecent[oa]s|oitocent[oa]s|novecent[oa]s"
    r"|mil)"
)
# "51 mil casos" é algarismo com milhar (conferido como número), não "mil" por extenso.
_NUMERO_EXTENSO = rf"(?<!\d)(?<!\d\s){_PALAVRA_NUMERO}(?:\s+e\s+{_PALAVRA_NUMERO}|\s+mil\b)*"
_EXTENSO = re.compile(
    rf"\b(?P<num>{_NUMERO_EXTENSO})\s+(?:[\w-]+\s+)?{_SUBSTANTIVO_CONTAGEM}\b", re.IGNORECASE
)
# Percentual por extenso ("vinte e oito por cento") não tem casa decimal a conferir: sempre barra.
_EXTENSO_PERCENTUAL = re.compile(rf"\b{_NUMERO_EXTENSO}\s*(?:por\s+cento\b|%)", re.IGNORECASE)
# Fração em palavras sobre uma contagem ("um terço dos casos") vale como o percentual que ela é,
# conferida como a proporção "1 em cada 3"; "metade do período" (sem contagem) não é dado.
_DENOMINADORES = {"terç": 3, "terc": 3, "quart": 4, "quint": 5, "décim": 10, "decim": 10}
_FRACAO_PALAVRA = re.compile(
    r"\b(?:(?P<metade>metade)|(?P<n>um|uma|dois|duas|tr[êe]s|quatro)\s+"
    r"(?P<d>ter[çc]|quart|quint|d[ée]cim)os?)"
    rf"\s+d[oa]s?\s+(?:[\w-]+\s+){{0,2}}?{_SUBSTANTIVO_CONTAGEM}\b",
    re.IGNORECASE,
)
# Variação em palavras: "dobraram" é +100%, "triplicou" +200%, "caiu pela metade" -50%.
_MULTIPLICADOR = re.compile(
    r"\b(?:(?P<dobro>dobr(?:ou|aram|ar|ando|am|a)|duplic(?:ou|aram|ar|ando)|(?:o|ao)\s+dobro)"
    r"|(?P<triplo>triplic(?:ou|aram|ar|ando)|(?:o|ao)\s+triplo)"
    r"|(?P<quadruplo>quadruplic(?:ou|aram|ar|ando)|(?:o|ao)\s+qu[áa]druplo)"
    r"|(?P<metade>pela\s+metade))\b",
    re.IGNORECASE,
)
_VARIACAO_MULTIPLICADOR = {"dobro": 100.0, "triplo": 200.0, "quadruplo": 300.0, "metade": -50.0}
_IDADE = re.compile(
    r"(?<![\w.,])(\d{1,3})(?:\s*(?:a|e|-|até)\s*(\d{1,3}))?\s*(?:anos?\b|\+)", re.IGNORECASE
)
# O grupo atômico impede o recuo de "12º" para "1"; ordinal ("1º", "2ª", "3°"; o NFKC faz de
# "º" um "o") não é número citado.
_NUMERO = re.compile(
    r"(?<![\w,])(?<!\d\.)(?P<sinal>[-+])?(?P<num>(?>\d+(?:[.,]\d+)*))(?![oaº°ª](?![^\W\d_]))"
    r"(?P<sufixo>\s*(?:%|por\s+cento\b)|\s+(?:mil|milh[õo]es|milh[ãa]o|bilh[õo]es|bilh[ãa]o)\b)?",
    re.IGNORECASE,
)
_FRASE = re.compile(r"(?<=[.!?])\s+(?=[^\s\[])")
_CONECTORES = r"(?:de|em|cerca|aproximadamente|quase|até|mais|menos|por|volta|da|do|a|o)"
_ANTES_DO_NUMERO = re.compile(rf"([\w-]+)\s+(?:{_CONECTORES}\s+){{0,3}}$", re.IGNORECASE)
_ALTA = re.compile(
    r"aument\w*|altas?|sub(?:iu|iram|ir|indo|ida|ido|iam|a|am)|cresc\w*|eleva\w*|avan[çc]\w*"
    r"|acr[ée]scimos?|salt\w*|dispar\w*",
    re.IGNORECASE,
)
_QUEDA = re.compile(
    r"quedas?|ca[ií](?:u|ram|r|ndo|am|a)?|redu\w*|diminu\w*|recu(?:o|ou|aram|ar|ando|a|am)"
    r"|baix\w*|decl[ií]n\w*|retra[çc]\w*|desacelera\w*",
    re.IGNORECASE,
)
_REMEDIO = (
    r"(?:medica\w*|rem[ée]dios?|antivira\w*|antibi[óo]tic\w*|oseltamivir|tamiflu|comprimid\w*"
    r"|c[áa]psulas?|doses?|xaropes?|gotas)"
)
_CLINICO = re.compile(
    r"\bprescri\w*|\bprescrev\w*|\bposologia\b|\bautomedica\w*"
    r"|\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|ml)\b"
    r"|\ba\s+cada\s+\d+\s+horas?\b|\bde\s+\d+\s+em\s+\d+\s+horas?\b"
    rf"|\b(?:tome|tomem|tomar|use|usem|utilize|utilizem|administre|administrem|aplique|apliquem"
    rf"|inicie|iniciem)\s+(?:\d+\s+|o\s+|a\s+|os\s+|as\s+|um\s+|uma\s+)?{_REMEDIO}"
    r"|\b(?:recomend|indic|aconselh|sugir|suger|orient)\w*(?:-se)?\s+(?:o\s+|a\s+|os\s+|as\s+)?"
    rf"(?:uso|tratamento|administra\w*|tomar|{_REMEDIO})"
    r"|\b(?:deve|devem|deve-se|precisa|precisam)\s+(?:tomar|usar|utilizar|iniciar|receber)\s+"
    rf"(?:o\s+|a\s+|um\s+|uma\s+)?(?:tratamento|{_REMEDIO})"
    r"|\btrat(?:e|em|ar)\s+com\b"
    # voz passiva: "o uso de antivirais é recomendado", "tratamento com X é indicado"
    rf"|\b(?:tratamento|{_REMEDIO})\b[^.!?\n]{{0,60}}?\b(?:é|são|foi|foram|seja|sejam|está|estão"
    r"|devem?\s+ser|podem?\s+ser)\s+(?:\w+\s+)?(?:recomendad|indicad|aconselhad|preconizad"
    r"|sugerid|orientad|prescrit)[oa]s?\b"
    # infinitivo como ordem: "procurar a unidade e iniciar oseltamivir"
    r"|\b(?:iniciar|tomar|usar|utilizar|administrar|aplicar)\s+"
    rf"(?:o\s+|a\s+|os\s+|as\s+|um\s+|uma\s+|\d+\s+)?{_REMEDIO}",
    re.IGNORECASE,
)
_HTML = re.compile(
    r"<\s*/?\s*[a-z!][\w:-]*(?:\s[^<>]*)?/?\s*>"
    r"|<\s*(?:script|iframe|img|svg|object|embed|style|link|meta)\b",
    re.IGNORECASE,
)
_CHAVES_FRACAO = frozenset({"valor", "percentual", "vacina_gripe"})
_CAMPOS_DE_DATA = frozenset({"periodo_inicio", "periodo_fim", "data_referencia"})
# Percentual citado a menos de 1 ponto de um valor do JSON: provável arredondamento errado
# ("27,9%" para 27,97%). A instrução mostra os vizinhos com o campo; o redator escolhe pelo sentido.
_JANELA_VIZINHO = 1.0
_MAX_VIZINHOS = 3
_MAX_DATAS = 12


# ---- modelos ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class NumeroCitado:
    bruto: str
    valor: float
    percentual: bool
    tolerancia: float
    inicio: int = 0  # posição no texto normalizado
    sinal: int = 0  # -1 ou +1 quando o texto escreve o sinal ("-20%")
    multiplicador: int = 1
    fim: int = 0  # posição logo depois do número (e do sufixo) no texto normalizado


class ResultadoSaida(BaseModel):
    texto: TextoRelatorio
    violacoes: list[str]
    dados_pessoais_mascarados: list[str]
    avisos: list[str] = []
    # Só para o LLM da nova tentativa: não vão à trilha nem ao relatório.
    instrucoes: list[str] = []
    # Ids das notícias que o texto cita ([n] ou a URL) e que existem, na ordem das seções, sem
    # repetir: a trilha registra essas notícias (o resultado da busca pode estar truncado).
    citadas: list[int] = []


def _pct(valor: float) -> str:
    """Percentual como o redator deve escrever: uma casa, vírgula, sem sinal."""
    return f"{abs(valor):.1f}".replace(".", ",") + "%"


def _variacao(valor: float) -> str:
    return f"{'alta' if valor > 0 else 'queda' if valor < 0 else 'variação'} de {_pct(valor)}"


@dataclass(frozen=True)
class _Rotulo:
    """Um percentual das evidências com o caminho dele no JSON que o redator vê."""

    campo: str  # "uti.valor", "aumento_casos.complemento.variacao_30d"
    valor: float  # fração vezes 100; com sinal quando é variação
    variacao: bool
    metrica: bool  # veio de consultar_metricas (não de uma categoria de distribuição)

    def __str__(self) -> str:
        return f"{self.campo} = {_variacao(self.valor) if self.variacao else _pct(self.valor)}"


@dataclass
class _Permitidos:
    fracoes: set[float] = field(default_factory=set)  # percentuais (fração vezes 100), sem sinal
    variacoes: set[float] = field(default_factory=set)  # percentuais COM sinal
    contagens: set[float] = field(default_factory=set)
    anos: set[int] = field(default_factory=set)
    datas: set[date] = field(default_factory=set)
    meses: set[tuple[int, int]] = field(default_factory=set)
    tem_rotulo_suprimido: bool = False  # há célula "<5"
    tem_supressao: bool = False  # há célula "<5" ou "oculto"
    categorias: set[str] = field(default_factory=set)  # "18-39", "60+", "<1"
    limites_idade: set[int] = field(default_factory=set)
    percentuais_noticia: dict[int, set[float]] = field(default_factory=dict)
    contagens_noticia: dict[int, set[float]] = field(default_factory=dict)
    # para a instrução de correção: o mesmo valor, com o campo de onde veio
    rotulos: list[_Rotulo] = field(default_factory=list)
    rotulos_data: dict[date, list[str]] = field(default_factory=dict)


# ---- normalização e extração -------------------------------------------------------------------
def _normalizar(texto: str) -> str:
    return normalizar_unicode(texto).translate(_TRACOS)


def _apagar(texto: str, inicio: int, fim: int) -> str:
    """Troca um trecho por espaços do mesmo tamanho: as posições do resto não mudam."""
    return texto[:inicio] + " " * (fim - inicio) + texto[fim:]


def _apagar_padrao(texto: str, padrao: re.Pattern[str]) -> str:
    return padrao.sub(lambda m: " " * len(m.group(0)), texto)


def _partes_numericas(numero: str) -> list[tuple[float, int]]:
    """'1.500' → 1500; '7,9' → 7,9 (1 casa); '10.0' → 10,0; listas ('1,2,3') → cada inteiro."""
    if numero.count(",") == 1:
        inteiro, decimais = numero.split(",")
        return [(float(inteiro.replace(".", "") + "." + decimais), len(decimais))]
    if "," in numero:
        return [(float(p.replace(".", "")), 0) for p in numero.split(",")]
    partes = numero.split(".")
    if len(partes) == 1 or (len(partes[0]) <= 3 and all(len(p) == 3 for p in partes[1:])):
        return [(float(numero.replace(".", "")), 0)]
    if len(partes) == 2:
        return [(float(numero), len(partes[1]))]
    return [(float(p), 0) for p in partes]


def _tokens(texto: str) -> list[tuple[NumeroCitado, bool]]:
    """(número, é_ano) de um texto já normalizado e com datas, links e rótulos apagados."""
    saida: list[tuple[NumeroCitado, bool]] = []
    for achado in _NUMERO.finditer(texto):
        sufixo = re.sub(r"\s+", " ", (achado.group("sufixo") or "").strip().lower())
        percentual = sufixo in ("%", "por cento")
        multiplicador = (
            1_000 if sufixo == "mil" else 1_000_000 if sufixo.startswith("milh")
            else 1_000_000_000 if sufixo.startswith("bilh") else 1
        )  # fmt: skip
        sinal = {"-": -1, "+": 1}.get(achado.group("sinal") or "", 0)
        numero = achado.group("num")
        ano = (
            not sufixo and not sinal and numero.isdigit() and len(numero) == 4
            and 1900 <= int(numero) <= 2100
        )  # fmt: skip
        for valor, casas in _partes_numericas(numero):
            meia_unidade = 0.5 * 10**-casas
            saida.append((
                NumeroCitado(
                    bruto=achado.group(0).strip(),
                    valor=valor if percentual else valor * multiplicador,
                    percentual=percentual,
                    tolerancia=meia_unidade if percentual else meia_unidade * multiplicador,
                    inicio=achado.start(),
                    sinal=sinal,
                    multiplicador=multiplicador,
                    fim=achado.end(),
                ),
                ano,
            ))  # fmt: skip
    return saida


def _datas_do_texto(texto: str) -> tuple[str, list[tuple[str, int | None, int, int | None]]]:
    """Apaga as datas do texto e devolve (texto, [(bruto, dia, mês, ano)])."""
    datas: list[tuple[str, int | None, int, int | None]] = []
    # Do mais longo para o mais curto: "06/2025" dentro de "25/06/2025" não é mês/ano.
    padroes = (
        (_DATA_EXTENSO, lambda m: (int(m[1]), _MESES[m[2].lower()], m[3] and int(m[3]))),
        (_MES_ANO_EXTENSO, lambda m: (None, _MESES[m[1].lower()], int(m[2]))),
        (_DATA_ISO, lambda m: (m[3] and int(m[3]), int(m[2]), int(m[1]))),
        (_DATA_BARRA, lambda m: (int(m[1]), int(m[2]), int(m[3]))),
        (_MES_ANO_BARRA, lambda m: (None, int(m[1]), int(m[2]))),
        (_DIA_MES_BARRA, lambda m: (int(m[1]), int(m[2]), None)),
    )
    for padrao, partes in padroes:
        for achado in list(padrao.finditer(texto)):
            dia, mes, ano = partes(achado)
            if ano is not None and ano < 100:
                ano += 2000
            datas.append((achado.group(0), dia or None, mes, ano or None))
            texto = _apagar(texto, achado.start(), achado.end())
    return texto, datas


def _limpar_para_numeros(texto: str) -> str:
    for padrao in (_URL_ESQUEMA, _WWW, _DOMINIO, CITACAO, _ROTULOS, _ROTULO_MENOS_DE_5):
        texto = _apagar_padrao(texto, padrao)
    return _apagar_padrao(_datas_do_texto(texto)[0], _CONTEXTOS_FIXOS)


def _ano_como_contagem(texto: str, numero: NumeroCitado) -> bool:
    """Inteiro com cara de ano (1900-2100) em posição de quantidade é contagem, não ano.

    (a) contagem logo depois ("2025 óbitos", "2025 novos casos"); (b) rótulo de contagem logo
    antes ("Óbitos: 2025") ou verbo de medida logo antes, numa oração que não fala de "ano(s)":
    no plural sem "de" basta o verbo ("foram 2025", "somaram 1938"); no singular ou com "de",
    a oração tem de falar de contagem ou de total ("o total de óbitos foi 2025").
    """
    if _CONTAGEM_DEPOIS.match(texto, numero.fim):
        return True
    oracao = _FIM_DE_ORACAO.split(texto[: numero.inicio])[-1]
    if _ROTULO_CONTAGEM.search(oracao):
        return True
    verbo = _VERBO_MEDIDA.search(oracao)
    if verbo is None or _FALA_DE_ANO.search(oracao):
        return False
    direto_no_plural = verbo.group("plural") is not None and verbo.group("de") is None
    return direto_no_plural or bool(_FALA_DE_CONTAGEM.search(oracao, 0, verbo.start()))


def extrair_numeros(texto: str) -> list[NumeroCitado]:
    """Números citados num texto (sem anos, datas, links, citações [n] nem rótulos "<5").

    Inteiro com cara de ano em posição de quantidade ("2025 óbitos", "foram 2025") é número.
    """
    limpo = _limpar_para_numeros(_normalizar(texto))
    return [n for n, ano in _tokens(limpo) if not ano or _ano_como_contagem(limpo, n)]


# ---- o que as evidências permitem --------------------------------------------------------------
def _registrar_data(texto: str, p: _Permitidos) -> None:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
            dia = date.fromisoformat(texto)
            p.datas.add(dia)
            p.meses.add((dia.year, dia.month))
            p.anos.add(dia.year)
        elif re.fullmatch(r"\d{4}-\d{2}", texto):
            ano, mes = map(int, texto.split("-"))
            p.meses.add((ano, mes))
            p.anos.add(ano)
    except ValueError:
        pass


def _coletar(valor: object, chave: str, eh_variacao: bool, p: _Permitidos) -> None:
    if isinstance(valor, bool) or valor is None:
        return
    if isinstance(valor, dict):
        for k, v in valor.items():
            _coletar(v, str(k), eh_variacao, p)
    elif isinstance(valor, list | tuple):
        for v in valor:
            _coletar(v, chave, eh_variacao, p)
    elif isinstance(valor, str):
        if valor == ROTULO_SUPRIMIDO:
            p.tem_rotulo_suprimido = p.tem_supressao = True
        elif valor == ROTULO_OCULTO:
            p.tem_supressao = True
        elif chave == "categoria" and any(c.isdigit() for c in valor):
            p.categorias.add(_normalizar(valor))
            p.limites_idade.update(int(n) for n in re.findall(r"\d+", valor))
        _registrar_data(valor, p)
    elif isinstance(valor, int | float) and math.isfinite(valor):
        if chave.startswith("variacao") or (chave == "valor" and eh_variacao):
            p.variacoes.add(valor * 100)
        elif chave in _CHAVES_FRACAO or chave.startswith("taxa"):
            p.fracoes.add(abs(valor) * 100)
        elif float(valor).is_integer():
            p.contagens.add(abs(float(valor)))
        else:  # fração sem nome conhecido
            p.fracoes.add(abs(valor) * 100)


def _rotular(valor: object, caminho: str, eh_variacao: bool, metrica: bool,
             p: _Permitidos) -> None:  # fmt: skip
    """Os percentuais e as datas de período de `_coletar`, com o caminho no JSON: a instrução
    de correção diz QUAL campo usar e nunca traz valor que não esteja nas evidências."""
    chave = caminho.rsplit(".", 1)[-1]
    if isinstance(valor, bool) or valor is None:
        return
    if isinstance(valor, dict):
        for k, v in valor.items():
            _rotular(v, f"{caminho}.{k}", eh_variacao, metrica, p)
    elif isinstance(valor, list):
        for item in valor:
            categoria = item.get("categoria") if isinstance(item, dict) else None
            destino = f"{caminho}.{categoria}" if isinstance(categoria, str) else caminho
            _rotular(item, destino, eh_variacao, metrica, p)
    elif isinstance(valor, str):
        if chave in _CAMPOS_DE_DATA and re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
            with contextlib.suppress(ValueError):
                p.rotulos_data.setdefault(date.fromisoformat(valor), []).append(caminho)
    elif isinstance(valor, int | float) and math.isfinite(valor):
        if chave.startswith("variacao") or (chave == "valor" and eh_variacao):
            p.rotulos.append(_Rotulo(caminho, valor * 100, True, metrica))
        elif chave in _CHAVES_FRACAO or chave.startswith("taxa"):
            p.rotulos.append(_Rotulo(caminho, abs(valor) * 100, False, metrica))


def _so_datas(valor: object, p: _Permitidos) -> None:
    if isinstance(valor, dict):
        for v in valor.values():
            _so_datas(v, p)
    elif isinstance(valor, list):
        for v in valor:
            _so_datas(v, p)
    elif isinstance(valor, str):
        _registrar_data(valor, p)


def _permitidos(evidencias: list[Evidencia], noticias: list[NoticiaCitavel]) -> _Permitidos:
    p = _Permitidos()
    for evidencia in evidencias:
        if evidencia.ferramenta == "buscar_noticias":
            _so_datas(evidencia.dados, p)  # números da notícia valem só citando-a (abaixo)
        elif evidencia.ferramenta == "consultar_dicionario":
            for trecho in evidencia.dados.get("trechos") or []:
                if isinstance(trecho.get("pagina"), int):
                    p.contagens.add(float(trecho["pagina"]))  # só a página; distância não
        else:
            eh_variacao = evidencia.dados.get("chave") == "aumento_casos"
            _coletar(evidencia.dados, "", eh_variacao, p)
            base = str(evidencia.dados.get("chave") or evidencia.dados.get("dimensao")
                       or evidencia.dados.get("tipo") or evidencia.ferramenta)  # fmt: skip
            metrica = evidencia.ferramenta == "consultar_metricas"
            _rotular(evidencia.dados, base, eh_variacao, metrica, p)
    for noticia in noticias:
        texto, datas = _datas_do_texto(_normalizar(f"{noticia.titulo} {noticia.trecho}"))
        for _bruto, dia, mes, ano in datas:
            if ano and _data_valida(dia, mes, ano):
                p.meses.add((ano, mes))
                p.anos.add(ano)
                if dia:
                    p.datas.add(date(ano, mes, dia))
        percentuais, contagens = set(), set()
        for numero, ano in _tokens(_limpar_para_numeros(texto)):
            if ano:
                p.anos.add(int(numero.valor))
            (percentuais if numero.percentual else contagens).add(numero.valor)
        p.percentuais_noticia[noticia.id] = percentuais
        p.contagens_noticia[noticia.id] = contagens
        if noticia.data:
            _registrar_data(noticia.data, p)
            with contextlib.suppress(ValueError):
                publicada = date.fromisoformat(noticia.data)
                p.rotulos_data.setdefault(publicada, []).append(f"notícia [{noticia.id}]")
    return p


# ---- conferências ------------------------------------------------------------------------------
def _perto(numero: NumeroCitado, alvos: set[float]) -> bool:
    return any(abs(numero.valor - v) <= numero.tolerancia + _EPSILON for v in alvos)


def _data_valida(dia: int | None, mes: int, ano: int | None) -> bool:
    try:
        date(ano or 2024, mes, dia or 1)  # 2024 é bissexto: 29/02 sem ano é válido
    except ValueError:
        return False
    return True


def _data_confere(dia: int | None, mes: int, ano: int | None, p: _Permitidos) -> bool:
    if not _data_valida(dia, mes, ano):
        return False
    if dia is None:
        return (ano, mes) in p.meses
    return any(d.day == dia and d.month == mes and (ano is None or d.year == ano)
               for d in p.datas)  # fmt: skip


def _sentido_do_texto(antes: str) -> int:
    achado = _ANTES_DO_NUMERO.search(antes)
    if not achado:
        return 0
    palavra = achado.group(1)
    if _ALTA.fullmatch(palavra):
        return 1
    if _QUEDA.fullmatch(palavra):
        return -1
    return 0


def _apagar_rotulos_dos_dados(texto: str, p: _Permitidos) -> str:
    for categoria in sorted(p.categorias, key=len, reverse=True):
        texto = _apagar_padrao(
            texto, re.compile(rf"(?<![\w.,]){re.escape(categoria)}(?![\w])", re.IGNORECASE)
        )
    for achado in list(_IDADE.finditer(texto)):
        limites = {int(n) for n in achado.groups() if n}
        if limites <= p.limites_idade:
            texto = _apagar(texto, achado.start(), achado.end())
    return _apagar_padrao(texto, _REGRA_SUPRESSAO)


def _host_e_caminho(endereco: str) -> tuple[str, str]:
    sem_esquema = re.sub(r"^[a-z][a-z0-9+.-]*://", "", endereco, flags=re.IGNORECASE)
    host, _, caminho = sem_esquema.partition("/")
    return host.lower(), caminho


def _instrucao_ano_contagem(numero: NumeroCitado) -> str:
    return (f"'{numero.bruto}' está em posição de quantidade (antes de casos, óbitos etc., ou "
            "depois de 'foram', 'somaram', 'o total ... foi' ou 'Óbitos:') e é lido como "
            "contagem, que não está no JSON. Se é o ano, escreva-o como data, com vírgula "
            "('Em 2025, os casos...'); se é contagem, use só uma contagem do JSON, com ponto de "
            "milhar, ou remova o número.")  # fmt: skip


def _trecho(texto: str) -> str:
    curto = texto[:_LIMITE_TRECHO] + ("…" if len(texto) > _LIMITE_TRECHO else "")
    return "[omitido: contém instrução]" if contem_injecao(curto) else curto


@dataclass
class _Secao:
    nome: str
    noticias: dict[int, NoticiaCitavel]
    p: _Permitidos
    violacoes: list[str] = field(default_factory=list)
    citadas: set[int] = field(default_factory=set)
    instrucoes: list[str] = field(default_factory=list)

    def links(self, frase: str) -> tuple[str, set[int]]:
        """Confere os links da frase; devolve a frase sem eles e as notícias que eles citam."""
        por_url = {n.url: n.id for n in self.noticias.values()}
        sem_esquema = {_host_e_caminho(u): i for u, i in por_url.items()}
        hosts = {_host_e_caminho(u)[0] for u in por_url}
        citadas: set[int] = set()
        for achado in _ESQUEMA_PERIGOSO.finditer(frase):
            self.violacoes.append(f"link_nao_verificado: {_trecho(achado.group(0))} em {self.nome}")
        for padrao in (_URL_ESQUEMA, _WWW, _DOMINIO):
            for achado in list(padrao.finditer(frase)):
                endereco = achado.group(0).rstrip(_PONTUACAO_FINAL)
                frase = _apagar(frase, achado.start(), achado.end())
                host, caminho = _host_e_caminho(endereco)
                if padrao is _URL_ESQUEMA:
                    identificador = por_url.get(endereco)
                elif caminho:
                    identificador = sem_esquema.get((host, caminho))
                else:  # só o site ("gov.br"): basta ser o site de uma notícia devolvida
                    identificador = 0 if any(h == host or h.endswith("." + host)
                                             for h in hosts) else None  # fmt: skip
                if identificador is None:
                    self.violacoes.append(
                        f"link_nao_verificado: {_trecho(endereco)} em {self.nome}"
                    )
                elif identificador:
                    citadas.add(identificador)
        return frase, citadas

    def numeros(self, frase: str, citadas: set[int]) -> None:
        p = self.p
        perc_citadas = set().union(*(p.percentuais_noticia.get(i, set()) for i in citadas))
        cont_citadas = set().union(*(p.contagens_noticia.get(i, set()) for i in citadas))
        for achado in _ROTULO_MENOS_DE_5.finditer(frase):
            if not p.tem_rotulo_suprimido:
                bruto = achado.group(0).strip()
                self._nao_conferido(bruto, None, f"'{bruto}' só vale quando o JSON traz '<5': "
                                                 "remova-o.")  # fmt: skip
        self._por_extenso(frase)
        self._em_palavras(frase, p.fracoes | perc_citadas)
        texto = _limpar_para_numeros(_apagar_rotulos_dos_dados(frase, p))
        texto = self._proporcoes(texto, p.fracoes | perc_citadas)
        for numero, ano in _tokens(texto):
            # "Foram 2025 óbitos": inteiro com cara de ano em posição de quantidade é contagem;
            # o ano das evidências não o salva.
            if ano and _ano_como_contagem(texto, numero):
                if not _perto(numero, p.contagens | cont_citadas):
                    self._nao_conferido(numero.bruto, numero, _instrucao_ano_contagem(numero))
            elif ano:
                if int(numero.valor) not in p.anos:
                    instrucao = f"o ano '{numero.bruto}' não aparece nas evidências: remova-o."
                    self._nao_conferido(numero.bruto, None, instrucao)
            elif numero.percentual:
                self._percentual(numero, texto[: numero.inicio], perc_citadas)
            elif (0 < numero.valor < LIMIAR_SUPRESSAO and numero.valor.is_integer()
                  and numero.multiplicador == 1
                  and _SEGUE_CONTAGEM.match(texto, numero.fim)):  # fmt: skip
                self._suprimido(numero.bruto)
            elif not _perto(numero, p.contagens | cont_citadas):
                self._nao_conferido(numero.bruto, numero)

    def _instruir(self, instrucao: str) -> None:
        self.instrucoes.append(f"Na seção {self.nome}, {instrucao}")

    def _suprimido(self, bruto: str) -> None:
        self.violacoes.append(f"valor_suprimido: '{bruto}' em {self.nome} "
                              "(contagens de 1 a 4 nunca são exibidas)")  # fmt: skip
        self._instruir(f"'{bruto}' é contagem de 1 a 4, que nunca é exibida: remova o número.")

    def _extenso_nao_conferido(self, bruto: str) -> None:
        self.violacoes.append(f"numero_nao_conferido: '{bruto}' em {self.nome} (número por "
                              "extenso não é conferido: escreva com algarismos)")  # fmt: skip
        self._instruir(f"'{bruto}' está por extenso: escreva com algarismos o valor exato do JSON "
                       "ou remova-o.")  # fmt: skip

    # ---- instruções de correção (só valores das evidências, sempre com o campo) -----------
    def _da_secao(self) -> list[_Rotulo]:
        """Percentuais da métrica de um comentário (comentario_uti → uti.*); vazio fora deles."""
        metrica = self.nome.removeprefix("comentario_")
        if metrica == self.nome:
            return []
        return [r for r in self.p.rotulos if r.metrica and r.campo.startswith(f"{metrica}.")]

    def _instrucao_fracao(self, bruto: str) -> str:
        da_secao = [r for r in self._da_secao() if not r.variacao]
        if len(da_secao) == 1:
            campo, valor = da_secao[0].campo, _pct(da_secao[0].valor)
            return (f"'{bruto}' é fração em palavras e não confere com o JSON: {campo} é {valor}. "
                    f"Escreva exatamente {valor} no lugar de '{bruto}', sem fração, aproximação "
                    "nem número por extenso.")  # fmt: skip
        opcoes = da_secao or [r for r in self.p.rotulos if r.metrica and not r.variacao]
        if not opcoes:
            return f"'{bruto}' é fração em palavras sem percentual no JSON que confirme: remova-a."
        return (f"'{bruto}' é fração em palavras e não confere com o JSON. Troque pelo percentual "
                "exato, com algarismos, do campo de que a frase fala "
                f"({'; '.join(map(str, opcoes))}) ou remova a fração.")  # fmt: skip

    def _instrucao_multiplicador(self, bruto: str) -> str:
        variacoes = [r for r in self.p.rotulos if r.metrica and r.variacao]
        exatas = (f"; para falar de variação, use só a exata: {'; '.join(map(str, variacoes))}"
                  if variacoes else "")  # fmt: skip
        return (f"'{bruto}' é variação em palavras que não está no JSON (nem quando uma notícia "
                f"a usa). Remova '{bruto}'{exatas}.")  # fmt: skip

    def _instrucao_percentual(self, numero: NumeroCitado) -> str:
        bruto = numero.bruto
        vizinhos = sorted(
            (r for r in self.p.rotulos if abs(abs(r.valor) - numero.valor) < _JANELA_VIZINHO),
            key=lambda r: abs(abs(r.valor) - numero.valor),
        )[:_MAX_VIZINHOS]
        if vizinhos:
            return (f"'{bruto}' não confere com o JSON (arredonde para uma casa, não trunque). "
                    f"Valores do JSON próximos: {'; '.join(map(str, vizinhos))}. Se a frase "
                    "fala de um deles, escreva exatamente esse valor; senão, remova o "
                    "número.")  # fmt: skip
        if da_secao := self._da_secao():
            exatos = "; ".join(map(str, da_secao))
            return (f"'{bruto}' não está no JSON. Nesta seção use só {exatos}, sem calcular, ou "
                    "remova o número.")  # fmt: skip
        return (f"'{bruto}' não está no JSON nem é arredondamento de um valor dele. Não calcule "
                "(diferença, soma, razão, média): use um valor do JSON com o mesmo significado ou "
                "remova o número.")  # fmt: skip

    def _instrucao_data(self, bruto: str) -> str:
        datas = sorted(self.p.rotulos_data.items())[:_MAX_DATAS]
        if not datas:
            return f"a data '{bruto}' não está nas evidências: remova-a."
        lista = "; ".join(f"{dia:%d/%m/%Y} ({', '.join(dict.fromkeys(campos))})"
                          for dia, campos in datas)  # fmt: skip
        return (f"a data '{bruto}' não está nas evidências (data sem ano tirada de notícia não é "
                f"conferida). Remova-a ou use só datas do JSON: {lista}; ou os dias e meses das "
                "séries.")  # fmt: skip

    def _por_extenso(self, frase: str) -> None:
        """Número por extenso não tem casa decimal a conferir: percentual e contagem barram.

        Contagem de 2 a 4 ("três casos") é valor que a supressão nunca exibe; "um caso" só conta
        quando há "<5" ou "oculto" nos dados (sem isso, "um" é artigo).
        """
        percentuais = [(a.start(), a.end()) for a in _EXTENSO_PERCENTUAL.finditer(frase)]
        for inicio, fim in percentuais:
            self._extenso_nao_conferido(frase[inicio:fim])
        for achado in _EXTENSO.finditer(frase):
            if any(i < achado.end() and achado.start() < f for i, f in percentuais):
                continue  # "cinquenta por cento dos casos" já foi barrado como percentual
            valor = _EXTENSO_PEQUENO.get(achado.group("num").lower())
            if valor is None:
                self._extenso_nao_conferido(achado.group(0))
            elif valor > 1 or self.p.tem_supressao:
                self._suprimido(achado.group(0))

    def _em_palavras(self, frase: str, fracoes: set[float]) -> None:
        """Fração ("um terço dos casos") e variação ("dobraram") em palavras são números: valem
        só se conferem como percentual inteiro, igual a "1 em cada 3" e a "+100%"."""
        for achado in _FRACAO_PALAVRA.finditer(frase):
            if achado.group("metade"):
                percentual = 50.0
            else:
                parte = _EXTENSO_PEQUENO[achado.group("n").lower()]
                todo = _DENOMINADORES[achado.group("d").lower()]
                percentual = 100 * parte / todo if parte < todo else None
            if percentual is None or not any(abs(percentual - f) <= 0.5 + _EPSILON
                                             for f in fracoes):  # fmt: skip
                bruto = achado.group(0)
                self._nao_conferido(bruto, None, self._instrucao_fracao(bruto))
        for achado in _MULTIPLICADOR.finditer(frase):
            variacao = _VARIACAO_MULTIPLICADOR[achado.lastgroup]
            if not any(abs(variacao - v) <= 0.5 + _EPSILON for v in self.p.variacoes):
                bruto = achado.group(0)
                self._nao_conferido(bruto, None, self._instrucao_multiplicador(bruto))

    def _proporcoes(self, texto: str, fracoes: set[float]) -> str:
        """Proporção ("1 em cada 10") vale como o percentual inteiro que ela é: 10,0% → "1 em cada
        10" e 75,0% → "3 em cada 4" passam; 32,2% → "1 em cada 3" (33,3%) não, como "33%" não
        passa. Devolve o texto sem as proporções."""
        for achado in list(_PROPORCAO.finditer(texto)):
            parte, todo = int(achado.group(1)), int(achado.group(2))
            percentual = 100 * parte / todo if 0 < parte < todo else None
            if percentual is None or not any(abs(percentual - f) <= 0.5 + _EPSILON
                                             for f in fracoes):  # fmt: skip
                bruto = achado.group(0)
                self._nao_conferido(bruto, None, self._instrucao_fracao(bruto))
            texto = _apagar(texto, achado.start(), achado.end())
        return texto

    def _percentual(self, numero: NumeroCitado, antes: str, das_citadas: set[float]) -> None:
        sentido = numero.sinal or _sentido_do_texto(antes)
        if _perto(numero, das_citadas):
            return
        if not sentido:
            if not _perto(numero, self.p.fracoes | {abs(v) for v in self.p.variacoes}):
                self._nao_conferido(numero.bruto, numero)
            return
        mesmo = {abs(v) for v in self.p.variacoes if v * sentido > 0}
        oposto = {abs(v) for v in self.p.variacoes if v * sentido < 0}
        if _perto(numero, mesmo):
            return
        if _perto(numero, oposto):
            real = "queda" if sentido > 0 else "alta"
            self.violacoes.append(f"sentido_invertido: '{numero.bruto}' em {self.nome} (nas "
                                  f"evidências a variação é de {real})")  # fmt: skip
            self._instruir(f"'{numero.bruto}' está com o sentido trocado: no JSON a variação é "
                           f"de {real}. Corrija o sentido (alta ou queda) ou remova o "
                           "número.")  # fmt: skip
        else:
            self._nao_conferido(numero.bruto, numero)

    def _nao_conferido(self, bruto: str, numero: NumeroCitado | None,
                       instrucao: str | None = None) -> None:  # fmt: skip
        """Violação curta (trilha e relatório) e, à parte, a instrução para a nova redação.

        Sem `instrucao` explícita, ela vem do número: da notícia (citar na mesma frase),
        percentual (vizinhos do JSON) ou contagem.
        """
        dica = ""
        if numero is not None:
            fonte = self.p.percentuais_noticia if numero.percentual else self.p.contagens_noticia
            if ids := [i for i, valores in fonte.items() if _perto(numero, valores)]:
                dica = (f" (número da notícia [{ids[0]}]: cite a notícia na mesma frase do "
                        "número)")  # fmt: skip
                instrucao = (f"'{bruto}' é número da notícia [{ids[0]}]: escreva [{ids[0]}] na "
                             "MESMA frase do número ou remova o número.")  # fmt: skip
            elif instrucao is None and numero.percentual:
                instrucao = self._instrucao_percentual(numero)
            elif instrucao is None:
                instrucao = (f"'{bruto}' não está no JSON: use só contagens que aparecem nele "
                             "(numerador, denominador, casos), sem somar nem subtrair, ou remova "
                             "o número.")  # fmt: skip
        self.violacoes.append(f"numero_nao_conferido: '{bruto}' em {self.nome}{dica}")
        if instrucao:
            self._instruir(instrucao)

    def datas(self, frase: str) -> None:
        for bruto, dia, mes, ano in _datas_do_texto(_apagar_padrao(frase, _URL_ESQUEMA))[1]:
            if not _data_confere(dia, mes, ano, self.p):
                self.violacoes.append(f"data_nao_conferida: '{bruto}' em {self.nome}")
                self._instruir(self._instrucao_data(bruto))


def _janela_exigida(secao: str) -> str | None:
    if secao == "cenario_atual":
        return "cenario_atual"
    if secao == "contexto_periodo_dados" or secao.startswith("comentario_"):
        return "periodo_dos_dados"
    return None


def _conferir_citacoes(secao: str, ids: list[int], noticias: dict[int, NoticiaCitavel],
                       avisos: list[str]) -> list[str]:  # fmt: skip
    violacoes = []
    exigida = _janela_exigida(secao)
    for identificador in dict.fromkeys(ids):
        noticia = noticias.get(identificador)
        if noticia is None:
            violacoes.append(f"citacao_inexistente: [{identificador}] em {secao}")
            continue
        if exigida and noticia.janela != exigida:
            de = "do cenário atual" if noticia.janela == "cenario_atual" else "do período dos dados"
            violacoes.append(f"noticia_fora_da_janela: [{identificador}] é {de} e foi usada em "
                             f"{secao}")  # fmt: skip
        try:
            publicada = date.fromisoformat(noticia.data) if noticia.data else None
        except ValueError:
            publicada = None
        if publicada is None:
            avisos.append(f"noticia_sem_data: [{identificador}] citada em {secao}; a janela não "
                          "pôde ser conferida pela data")  # fmt: skip
        elif (
            noticia.inicio_janela
            and noticia.fim_janela
            and not (noticia.inicio_janela <= publicada <= noticia.fim_janela)
        ):
            violacoes.append(
                f"noticia_fora_da_janela: [{identificador}] datada de {publicada.isoformat()}, "
                f"fora da busca de {noticia.inicio_janela.isoformat()} a "
                f"{noticia.fim_janela.isoformat()}, em {secao}"
            )
    return violacoes


def _conferir_secao(nome: str, texto: str, noticias: dict[int, NoticiaCitavel], p: _Permitidos,
                    avisos: list[str]) -> tuple[list[str], list[str], list[int]]:  # fmt: skip
    """(violações, instruções de correção, ids citados) de uma seção."""
    secao = _Secao(nome, noticias, p)
    if achado := _HTML.search(texto):
        secao.violacoes.append(f"marcacao_html: '{_trecho(achado.group(0))}' em {nome}")
    if contem_injecao(texto):
        secao.violacoes.append(
            f"instrucao_suspeita: texto com instrução dirigida a modelo em {nome}"
        )
    if achado := _CLINICO.search(texto):
        secao.violacoes.append(f"conteudo_clinico: '{_trecho(achado.group(0))}' em {nome}")
    ids_secao: list[int] = []
    for frase in _FRASE.split(texto):
        sem_links, citadas_por_link = secao.links(frase)
        citadas = {int(i) for grupo in CITACAO.findall(sem_links)
                   for i in re.split(r"\s*[,;]\s*", grupo)}  # fmt: skip
        ids_secao += sorted(citadas) + sorted(citadas_por_link)
        secao.datas(sem_links)
        secao.numeros(sem_links, citadas | citadas_por_link)
    violacoes = secao.violacoes + _conferir_citacoes(nome, ids_secao, noticias, avisos)
    return violacoes, secao.instrucoes, ids_secao


def verificar_saida(texto: TextoRelatorio, evidencias: list[Evidencia],
                    noticias: list[NoticiaCitavel]) -> ResultadoSaida:  # fmt: skip
    mascaradas: dict[str, str] = {}
    pii: list[str] = []
    for nome, conteudo in texto.secoes().items():
        mascaradas[nome], encontrados = mascarar_pii(conteudo)
        pii += encontrados

    permitidos = _permitidos(evidencias, noticias)
    por_id = {n.id: n for n in noticias}
    violacoes: list[str] = []
    instrucoes: list[str] = []
    avisos: list[str] = []
    citadas: list[int] = []
    for nome, conteudo in mascaradas.items():
        da_secao, instrucoes_da_secao, ids = _conferir_secao(
            nome, _normalizar(conteudo), por_id, permitidos, avisos
        )
        violacoes += da_secao
        instrucoes += instrucoes_da_secao
        citadas += [i for i in ids if i in por_id]
    # Segunda barreira: a mensagem repete trechos do texto; nenhum dado pessoal sai por ela.
    violacoes = list(dict.fromkeys(mascarar_pii(v)[0] for v in violacoes))
    instrucoes = list(dict.fromkeys(mascarar_pii(i)[0] for i in instrucoes))[:LIMITE_VIOLACOES]
    if len(violacoes) > LIMITE_VIOLACOES:
        excedentes = len(violacoes) - LIMITE_VIOLACOES
        violacoes = [*violacoes[:LIMITE_VIOLACOES], f"violacoes_truncadas: mais {excedentes}"]
    return ResultadoSaida(
        texto=texto.com_secoes(mascaradas),
        violacoes=violacoes,
        dados_pessoais_mascarados=pii,
        avisos=list(dict.fromkeys(mascarar_pii(a)[0] for a in avisos)),
        instrucoes=instrucoes,
        citadas=list(dict.fromkeys(citadas)),
    )
