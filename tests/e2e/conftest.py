"""Fixtures dos E2E: o banco `srag_teste` e a amostra vêm de `tests/conftest.py`."""

import pytest


@pytest.fixture
def pasta_saida(tmp_path):
    return tmp_path / "relatorios"
