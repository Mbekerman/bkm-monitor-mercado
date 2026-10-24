"""Esqueleto da API oficial v2 (OAuth2 client credentials).

Pronto para receber ORULO_CLIENT_ID e ORULO_CLIENT_SECRET. Sem as duas
variáveis, não chama a rede. O contrato é o da API oficial v2:
token em POST /oauth/token e os recursos em /api/v2/buildings.
"""
from __future__ import annotations

import logging

from coletar.erros import OruloAuthError, OruloBlocked, OruloError
from coletar.fonte import ContextoColeta, ResultadoFonte, deve_abrir, precisa_unidades
from coletar.mapeamento import mapear_empreendimento, situacao_empreendimento
from coletar.orulo_http import ClienteHttp

log = logging.getLogger("coletar")

BASE = "https://www.orulo.com.br"
TOKEN_URL = f"{BASE}/oauth/token"
PARAMS_UNIDADES = [
    ("include[]", "not_available"),
    ("status[]", "available"),
    ("status[]", "reserved"),
    ("status[]", "sold"),
]


class OruloApi:
    nome = "orulo_api"

    def __init__(self, *, client_id: str, client_secret: str, atraso=(2.0, 3.0), sleep=None, transporte=None):
        self.client_id = client_id or ""
        self.client_secret = client_secret or ""
        self._token: str | None = None
        kw = {"atraso": atraso, "transporte": transporte}
        if sleep is not None:
            kw["sleep"] = sleep
        self.http = ClienteHttp(**kw)
        self.http.cabecalhos_auth = self._bearer
        self.http.renovar = self._obter_token

    def fechar(self) -> None:
        self.http.fechar()

    def _bearer(self) -> dict:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    def _credenciais(self) -> None:
        if not self.client_id or not self.client_secret:
            raise OruloAuthError("ORULO_CLIENT_ID e ORULO_CLIENT_SECRET são obrigatórios")

    def _obter_token(self) -> None:
        self._credenciais()
        resp = self.http.post_direto(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise OruloAuthError(f"token da API falhou ({resp.status_code})")
        token = resp.json().get("access_token")
        if not token:
            raise OruloAuthError("token da API sem access_token")
        self._token = token
        log.info("token da API oficial ok")

    def _json(self, method: str, url: str, **kw):
        r = self.http.pedir(method, url, auth=True, **kw)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise OruloError(f"{url} retornou {r.status_code}")
        return r.json()

    def listar(self, uf: str, cidade: str) -> list[dict]:
        saida = []
        pagina = 1
        while pagina <= 100:
            data = self._json(
                "GET", f"{BASE}/api/v2/buildings",
                params=[
                    ("state", uf),
                    ("city", cidade),
                    ("status[]", "ready"),
                    ("status[]", "under_construction"),
                    ("finality[]", "residential"),
                    ("page", str(pagina)),
                    ("results_per_page", "500"),
                ],
            )
            if not isinstance(data, dict):
                break
            lote = data.get("buildings") or []
            saida.extend(b for b in lote if isinstance(b, dict) and b.get("id") is not None)
            total = int(data.get("total_pages") or 1)
            if pagina >= total or not lote:
                break
            pagina += 1
        return saida

    def detalhe(self, building_id):
        return self._json("GET", f"{BASE}/api/v2/buildings/{building_id}")

    def unidades(self, building_id) -> list | None:
        data = self._json(
            "GET", f"{BASE}/api/v2/buildings/{building_id}/units",
            params=PARAMS_UNIDADES,
        )
        if data is None:
            return None
        if isinstance(data, dict):
            return data.get("units") or []
        if isinstance(data, list):
            return data
        raise OruloError(f"unidades {building_id} em formato inesperado")

    def coletar(self, ctx: ContextoColeta) -> ResultadoFonte:
        self._credenciais()
        emps: list[dict] = []
        itens: list[dict] = []
        falhas: list[dict] = []
        esperados = 0
        forcar = False
        falhou = False
        motivo = None

        def visitar(bid, lista) -> None:
            nonlocal esperados, forcar, falhou, motivo
            if forcar or falhou or not deve_abrir(ctx, str(bid)):
                return
            esperados += 1
            try:
                emp, item, erro = self._um(ctx, bid, lista)
            except OruloBlocked as e:
                falhas.append({"id_externo": str(bid), "motivo": "bloqueado", "detalhe": str(e)[:500]})
                forcar = True
                return
            except OruloAuthError as e:
                falhou = True
                motivo = str(e)
                falhas.append({"id_externo": str(bid), "motivo": "autenticacao", "detalhe": str(e)[:500]})
                return
            if erro:
                falhas.append(erro)
                return
            emps.append(emp)
            itens.append(item)

        try:
            bloqueado_no_token = None
            try:
                self._obter_token()
            except OruloBlocked as e:
                bloqueado_no_token = e
            if bloqueado_no_token is not None:
                falhas.append({"id_externo": "", "motivo": "bloqueado", "detalhe": str(bloqueado_no_token)[:500]})
                forcar = True
            elif ctx.ids:
                for bid in ctx.ids:
                    visitar(bid, {"id": str(bid)})
            else:
                for mun in ctx.municipios:
                    if forcar or falhou:
                        break
                    try:
                        lista = self.listar(mun.uf, mun.nome)
                    except OruloBlocked as e:
                        falhas.append({"id_externo": "", "motivo": "bloqueado", "detalhe": str(e)[:500]})
                        forcar = True
                        break
                    except OruloAuthError as e:
                        falhou = True
                        motivo = str(e)
                        break
                    except OruloError as e:
                        falhas.append({"id_externo": "", "motivo": "descoberta", "detalhe": f"{mun.nome}: {e}"[:500]})
                        forcar = True
                        continue
                    for bruto in lista:
                        visitar(bruto.get("id"), bruto)
                        if forcar or falhou:
                            break
        finally:
            self.fechar()
        return ResultadoFonte(
            empreendimentos=emps,
            bruto={"fonte": self.nome, "tipo": ctx.tipo, "referencia": ctx.referencia.isoformat(), "itens": itens},
            esperados=esperados,
            falhas=falhas,
            forcar_incompleta=forcar,
            falhou=falhou,
            motivo_falha=motivo,
            avisos=["A API oficial ainda não foi exercitada com credenciais reais; o cliente segue o OpenAPI v2."],
        )

    def _um(self, ctx, bid, lista):
        detalhe = self.detalhe(bid)
        if detalhe is None:
            return None, None, {"id_externo": str(bid), "motivo": "nao_encontrado", "detalhe": "detalhe 404"}
        base = detalhe
        situacao = situacao_empreendimento(base.get("status"), base.get("stage"))
        incluir = precisa_unidades(ctx, str(bid), situacao)
        uns = None
        if incluir:
            uns = self.unidades(bid)
            if uns is None:
                return None, None, {"id_externo": str(bid), "motivo": "nao_encontrado", "detalhe": "unidades 404"}
        emp = mapear_empreendimento(lista, detalhe, uns, incluir_unidades=incluir)
        if emp.get("latitude") is None or emp.get("longitude") is None:
            return None, None, {"id_externo": str(bid), "motivo": "sem_coordenada", "detalhe": emp.get("nome") or ""}
        return emp, {"id": str(bid), "lista": lista, "detalhe": detalhe, "unidades": uns}, None
