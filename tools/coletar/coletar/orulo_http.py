"""HTTP educado: espera entre pedidos, tenta de novo em erro temporário, para em 429/403.

O token não entra em log. Quem chama injeta o transporte nos testes; sem isso,
o cliente fala com a rede de verdade.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable

import httpx

from coletar.erros import OruloAuthError, OruloBlocked, OruloError

log = logging.getLogger("coletar")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class ClienteHttp:
    def __init__(self, *, atraso: tuple[float, float] = (2.0, 3.0), sleep: Callable[[float], None] = time.sleep,
                 transporte: httpx.BaseTransport | None = None):
        self.atraso = atraso
        self._sleep = sleep
        self._ultimo = 0.0
        self.pedidos = 0
        self.renovar: Callable[[], None] | None = None
        self.cabecalhos_auth: Callable[[], dict] = lambda: {}
        self.http = httpx.Client(
            transport=transporte,
            timeout=httpx.Timeout(60.0, connect=20.0),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            },
            follow_redirects=False,
        )

    def fechar(self) -> None:
        self.http.close()

    def _esperar(self) -> None:
        minimo, maximo = self.atraso
        if maximo < minimo:
            minimo, maximo = maximo, minimo
        espera = random.uniform(minimo, maximo) if maximo > 0 else 0.0
        if self._ultimo:
            falta = espera - (time.monotonic() - self._ultimo)
            if falta > 0:
                self._sleep(falta)

    def post_direto(self, url: str, **kw) -> httpx.Response:
        """Um POST fora do ciclo de 401 (login e token). Não reloga."""
        self._esperar()
        try:
            resp = self.http.post(url, **kw)
        finally:
            self._ultimo = time.monotonic()
            self.pedidos += 1
        if resp.status_code in (429, 403):
            raise OruloBlocked(f"{resp.status_code} em POST {url}")
        return resp

    def pedir(self, method: str, url: str, *, auth: bool = False, **kw) -> httpx.Response:
        tentativas = 0
        relogou = False
        extra = kw.pop("headers", None) or {}
        while True:
            self._esperar()
            headers = dict(extra)
            if auth:
                headers.update(self.cabecalhos_auth())
            try:
                resp = self.http.request(method, url, headers=headers, **kw)
            except httpx.TransportError as e:
                tentativas += 1
                self._ultimo = time.monotonic()
                if tentativas >= 3:
                    raise OruloError(f"erro de rede em {method} {url}: {type(e).__name__}") from e
                log.warning("erro de rede (%s), tentativa %d/3", type(e).__name__, tentativas)
                self._sleep(10 * tentativas)
                continue
            self._ultimo = time.monotonic()
            self.pedidos += 1
            if resp.status_code in (429, 403):
                raise OruloBlocked(f"{resp.status_code} em {method} {url}")
            if resp.status_code == 401 and auth:
                if relogou or self.renovar is None:
                    raise OruloAuthError("401 mesmo após relogin")
                log.info("401: refazendo o login uma vez")
                self.renovar()
                relogou = True
                continue
            if resp.status_code >= 500:
                tentativas += 1
                if tentativas >= 3:
                    raise OruloError(f"{resp.status_code} em {method} {url}")
                log.warning("%s em %s, tentativa %d/3", resp.status_code, url, tentativas)
                self._sleep(15 * tentativas)
                continue
            return resp
