import pytest

from coletar.cli import resolver_fonte
from coletar.erros import ColetaErro
from coletar.fonte import fonte_ids


def test_fonte_padrao_e_login(monkeypatch):
    monkeypatch.delenv("COLETAR_FONTE", raising=False)
    assert resolver_fonte(None) == "orulo_login"


def test_coletar_fonte_troca_a_fonte_do_job(monkeypatch):
    monkeypatch.setenv("COLETAR_FONTE", "orulo_api")
    assert resolver_fonte(None) == "orulo_api"
    assert resolver_fonte("orulo_login") == "orulo_login"  # flag ganha do ambiente


def test_coletar_fonte_invalida(monkeypatch):
    monkeypatch.setenv("COLETAR_FONTE", "api")
    with pytest.raises(ColetaErro):
        resolver_fonte(None)


def test_api_usa_os_ids_do_login():
    assert fonte_ids("orulo_api") == "orulo_login"
    assert fonte_ids("orulo_login") == "orulo_login"
    assert fonte_ids("orulo_planilha") == "orulo_planilha"
