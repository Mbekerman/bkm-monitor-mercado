"""Adaptador orulo_login.

Login em auth.orulo.com.br, descoberta no elastic público (fatiada por bbox),
detalhe e unidades no portal. Credenciais só em ORULO_EMAIL e ORULO_PASSWORD.
O client id abaixo é o do próprio site, público; não é segredo.
"""
from __future__ import annotations

import logging

from coletar.descoberta import descobrir, params_cidade
from coletar.erros import OruloAuthError, OruloBlocked, OruloError
from coletar.fonte import ContextoColeta, ResultadoFonte, precisa_unidades
from coletar.mapeamento import mapear_empreendimento, situacao_empreendimento
from coletar.orulo_http import ClienteHttp

log = logging.getLogger("coletar")

AUTH_URL = "https://auth.orulo.com.br/api/login"
BASE = "https://www.orulo.com.br"
# Público: é o client do próprio site, conferido em 24/out/2026. Não é senha.
CLIENT_ID = "TKYZFTALAu1tshVidMJdV15BKz97ghBs_xaoarFpCiY"
PARAMS_UNIDADES = [
    ("include[]", "not_available"),
    ("status[]", "available"),
    ("status[]", "reserved"),
    ("status[]", "sold"),
]


class OruloLogin:
    nome = "orulo_login"

    def __init__(self, *, email: str, senha: str, atraso=(2.0, 3.0), sleep=None, transporte=None):
        self.email = email or ""
        self.senha = senha or ""
        self._token: str | None = None
        kw = {"atraso": atraso, "transporte": transporte}
        if sleep is not None:
            kw["sleep"] = sleep
        self.http = ClienteHttp(**kw)
        self.http.cabecalhos_auth = self._cookie
        self.http.renovar = self.login

    def fechar(self) -> None:
        self.http.fechar()

    def _cookie(self) -> dict:
        if not self._token:
            return {}
        return {"Cookie": f"user_access_token={self._token}"}

    def login(self) -> None:
        if not self.email or not self.senha:
            raise OruloAuthError("ORULO_EMAIL e ORULO_PASSWORD são obrigatórios")
        resp = self.http.post_direto(
            AUTH_URL,
            json={
                "grant_type": "password",
                "email": self.email,
                "password": self.senha,
                "clientId": CLIENT_ID,
            },
            headers={
                "Content-Type": "application/json",
                "Origin": "https://auth.orulo.com.br",
                "Referer": "https://auth.orulo.com.br/",
            },
        )
        if resp.status_code != 200:
            msg = ""
            try:
                corpo = resp.json()
                msg = str(corpo.get("error_description") or corpo.get("message") or "")[:120]
            except Exception:
                msg = ""
            raise OruloAuthError(f"login falhou ({resp.status_code}) {msg}".strip())
        token = resp.json().get("access_token")
        if not token:
            raise OruloAuthError("login sem access_token")
        self._token = token
        log.info("login no Órulo ok")

    def buscar(self, params: list[tuple[str, str]]) -> dict:
        r = self.http.pedir(
            "POST", f"{BASE}/api/elastic", params=params, json={},
            headers={"Content-Type": "application/json", "Origin": BASE, "Referer": f"{BASE}/buildings"},
        )
        if r.status_code != 200:
            raise OruloError(f"/api/elastic retornou {r.status_code}")
        return r.json()

    def detalhe(self, building_id) -> dict | None:
        r = self.http.pedir(
            "GET", f"{BASE}/api/portal/buildings/{building_id}",
            headers={"Referer": f"{BASE}/buildings/{building_id}"},
        )
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise OruloError(f"detalhe {building_id} retornou {r.status_code}")
        return r.json()

    def unidades(self, building_id) -> list | None:
        r = self.http.pedir(
            "GET", f"{BASE}/api/portal/buildings/{building_id}/units",
            params=PARAMS_UNIDADES, auth=True,
            headers={"Referer": f"{BASE}/buildings/{building_id}/units"},
        )
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise OruloError(f"unidades {building_id} retornaram {r.status_code}")
        data = r.json()
        if isinstance(data, dict):
            return data.get("units") or []
        if isinstance(data, list):
            return data
        raise OruloError(f"unidades {building_id} em formato inesperado")

    def coletar(self, ctx: ContextoColeta) -> ResultadoFonte:
        emps: list[dict] = []
        itens: list[dict] = []
        falhas: list[dict] = []
        esperados = 0
        forcar = False
        falhou = False
        motivo = None
        try:
            bloqueado_no_login = None
            try:
                self.login()
            except OruloBlocked as e:
                bloqueado_no_login = e
            if bloqueado_no_login is not None:
                falhas.append({"id_externo": "", "motivo": "bloqueado", "detalhe": str(bloqueado_no_login)[:500]})
                forcar = True
            for mun in ctx.municipios:
                if forcar or falhou:
                    break
                try:
                    achados, stats = descobrir(
                        self.buscar, params_base=params_cidade(mun.uf, mun.nome),
                        bbox=mun.bbox, cidade=mun.nome,
                    )
                except OruloBlocked as e:
                    falhas.append({"id_externo": "", "motivo": "bloqueado", "detalhe": str(e)[:500]})
                    forcar = True
                    break
                except OruloError as e:
                    falhas.append({"id_externo": "", "motivo": "descoberta", "detalhe": f"{mun.nome}: {e}"[:500]})
                    forcar = True
                    continue
                if stats["folhas_truncadas"]:
                    forcar = True
                    falhas.append({
                        "id_externo": "", "motivo": "descoberta",
                        "detalhe": f"{mun.nome}: fatia ainda cheia no limite de profundidade",
                    })
                esperados += len(achados)
                for bid, lista in achados.items():
                    try:
                        emp, bruto_item, erro = self._um(ctx, bid, lista)
                    except OruloBlocked as e:
                        falhas.append({"id_externo": str(bid), "motivo": "bloqueado", "detalhe": str(e)[:500]})
                        forcar = True
                        break
                    except OruloAuthError as e:
                        falhou = True
                        motivo = str(e)
                        falhas.append({"id_externo": str(bid), "motivo": "autenticacao", "detalhe": str(e)[:500]})
                        break
                    if erro:
                        falhas.append(erro)
                        continue
                    emps.append(emp)
                    itens.append(bruto_item)
                if forcar or falhou:
                    break
        finally:
            self.fechar()
        return ResultadoFonte(
            empreendimentos=emps,
            bruto={
                "fonte": self.nome,
                "tipo": ctx.tipo,
                "referencia": ctx.referencia.isoformat(),
                "itens": itens,
            },
            esperados=esperados,
            falhas=falhas,
            forcar_incompleta=forcar,
            falhou=falhou,
            motivo_falha=motivo,
        )

    def _um(self, ctx: ContextoColeta, bid, lista: dict):
        detalhe = None
        if ctx.tipo == "completa_mensal":
            detalhe = self.detalhe(bid)
            if detalhe is None:
                return None, None, {"id_externo": str(bid), "motivo": "nao_encontrado", "detalhe": "detalhe 404"}
        base = detalhe or lista
        situacao = situacao_empreendimento(base.get("status"), base.get("stage"))
        uns = None
        incluir = precisa_unidades(ctx, str(bid), situacao)
        if incluir:
            uns = self.unidades(bid)
            if uns is None:
                return None, None, {"id_externo": str(bid), "motivo": "nao_encontrado", "detalhe": "unidades 404"}
        emp = mapear_empreendimento(lista, detalhe, uns, incluir_unidades=incluir)
        if emp.get("latitude") is None or emp.get("longitude") is None:
            return None, None, {"id_externo": str(bid), "motivo": "sem_coordenada", "detalhe": emp.get("nome") or ""}
        return emp, {"id": str(bid), "lista": lista, "detalhe": detalhe, "unidades": uns}, None
