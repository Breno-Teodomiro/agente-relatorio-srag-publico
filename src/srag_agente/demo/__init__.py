"""Demonstração online: constantes de contrato entre o pacote, a carga, o teto e a tela.

Ficam aqui, num lugar só, para que exportação, carga, conferência e testes leiam a mesma lista.
"""

import ipaddress
import socket
from typing import Literal

# tabela: (colunas permitidas, ORDER BY determinístico). Coluna nova na gold não vai para a
# nuvem sem decisão: a exportação compara com esta lista e falha se aparecer uma que não consta.
TABELAS_DEMO: dict[str, tuple[tuple[str, ...], str]] = {
    "referencia": (("data_referencia", "data_consolidada", "inicio_base", "total_casos"), "1"),
    "casos_diarios": (("uf", "dia", "casos"), "uf, dia"),
    "indicadores": (
        (
            "uf",
            "total_casos",
            "obitos",
            "com_desfecho",
            "uti_sim",
            "uti_informada",
            "vacina_covid_sim",
            "vacina_covid_informada",
            "vacina_gripe_sim",
            "vacina_gripe_informada",
            "periodo_inicio",
            "periodo_fim",
        ),
        "uf",
    ),
    "janelas_aumento": (
        (
            "uf",
            "casos_7d_atual",
            "casos_7d_anterior",
            "casos_30d_atual",
            "casos_30d_anterior",
            "casos_em_consolidacao",
            "data_consolidada",
        ),
        "uf",
    ),
    "distribuicao": (("dimensao", "uf", "categoria", "casos"), "dimensao, uf, categoria"),
    "dicionario_trechos": (("id", "campo", "pagina", "texto", "embedding"), "id"),
}

# Nomes (minúsculos) que nunca entram no pacote; `*` é curinga no estilo fnmatch. É a regra do
# ADR 0004: identificadores, município, raça/etnia, nascimento e texto livre ficam na `raw`.
COLUNAS_PROIBIDAS: tuple[str, ...] = (
    "nu_notific",
    "dt_nasc",
    "id_municip",
    "co_mun_not",
    "co_mun_res",
    "id_mn_resi",
    "cs_raca",
    "cs_sexo",
    "nm_*",
    "obs*",
    "id_caso",
    "*_out",
    # Demais colunas que o ADR 0004 cita por nome (escolaridade, ocupação, documento, povo,
    # nacionalidade, lote de vacina) e unidade de saúde, que localiza tanto quanto o município.
    "cs_etinia",
    "cs_escol_n",
    "pac_cocbo",
    "pac_dscbo",
    "tem_cpf",
    "pov_ct",
    "estrang",
    "lote_*",
    "id_unidade",
    "co_uni_not",
    "id_mn_*",
    "co_mu_*",
)

# SQLSTATE próprio do gatilho do teto diário (classe `SR`, que o Postgres não usa).
SQLSTATE_TETO = "SR429"
# Trava consultiva do gatilho, vizinha da trava da transformação (7350301).
TRAVA_TETO = 7_350_302
FUSO_DEMO = "America/Sao_Paulo"

# Hosts que são a máquina ou o Compose locais: o modo demo e a carga os recusam como alvo.
HOSTS_LOCAIS: frozenset[str] = frozenset(
    {"localhost", "127.0.0.1", "::1", "banco", "host.docker.internal"}
)
# Modos de TLS aceitos em banco remoto (sem `disable`, `allow` nem `prefer`).
SSLMODES_SEGUROS: frozenset[str] = frozenset({"require", "verify-ca", "verify-full"})


def _ip_local(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified
            or ip.is_multicast)  # fmt: skip


def classificar_host(host: str | None) -> Literal["local", "remoto", "sem_resolucao"]:
    """ "local" se o host é (ou resolve para) a própria máquina ou rede interna; "remoto" se todo
    endereço é público; "sem_resolucao" se o nome não resolve (quem chama decide, nunca libera).

    Formas numéricas que a libc aceita (`127.1`, `2130706433`, `0x7f000001`, `0177.0.0.1`) são
    lidas como a libpq as leria. Nome passa pelo DNS: basta UM endereço local para ser local
    (`127.0.0.1.nip.io`, `localtest.me`). O DNS pode mudar entre a conferência e a conexão;
    a carga ainda exige TLS (e `verify-full` para mandar senha em texto).
    """
    nome = (host or "").strip().lower().rstrip(".")
    if not nome or nome.startswith("/") or "," in nome:
        return "local"
    if nome.startswith("[") and nome.endswith("]"):
        nome = nome[1:-1]
    if nome in HOSTS_LOCAIS or nome.endswith((".local", ".localhost")):
        return "local"
    try:
        return "local" if _ip_local(ipaddress.ip_address(nome)) else "remoto"
    except ValueError:
        pass
    try:  # IPv4 em forma legada (curta, decimal, hexadecimal, octal)
        return "local" if _ip_local(ipaddress.IPv4Address(socket.inet_aton(nome))) else "remoto"
    except OSError:
        pass
    try:
        enderecos = {info[4][0].split("%", 1)[0] for info in socket.getaddrinfo(nome, None)}
    except (OSError, UnicodeError):
        return "sem_resolucao"
    if not enderecos:
        return "sem_resolucao"
    return "local" if any(_ip_local(ipaddress.ip_address(e)) for e in enderecos) else "remoto"


def host_local(host: str | None) -> bool:
    """Regra única (carga da demo e tela) de "não é o banco remoto": vazio, socket Unix, lista de
    hosts, nome da máquina ou do Compose, `.local`/`.localhost`, IP (inclusive em forma legada)
    de loopback, rede interna, link-local, não especificado ou multicast, e nome que resolve
    para algum desses. Nome que NÃO resolve devolve True (o valor conservador: na dúvida, não é
    o banco remoto)."""
    return classificar_host(host) != "remoto"
