"""Entrada da demonstração online na plataforma de hospedagem. Localmente, use `make app`.

A plataforma procura o arquivo de dependências primeiro na pasta da entrada (`demo/`); o pacote
não é instalado, entra pelo `sys.path`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from srag_agente.app import main

main()
