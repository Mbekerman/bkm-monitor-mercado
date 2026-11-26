"""Adaptador orulo_api: API oficial v2 do Órulo (OAuth2 client credentials).

Credenciais só em ORULO_CLIENT_ID e ORULO_CLIENT_SECRET (Secret Manager); sem
as duas, não chama a rede. Contrato: OpenAPI v2.0.92 de documento.orulo.com.br,
conferido com a integração de homologação da BKM em 26/nov/2026.

- Token: POST /oauth/token (client_credentials), validade de cerca de 1 ano.
- Listagem: GET /api/v2/buildings por UF e cidade, com os mesmos filtros da
  descoberta do orulo_login (obra e pronto novo, residencial). A API devolve no
  máximo 50 por página, mesmo pedindo mais, e no máximo 10 mil por filtro
  (`results_limit_exceeded`). Paginamos por `total_pages`.
- Detalhe: GET /api/v2/buildings/{id}?include[]=not_available.
- Unidades: GET /api/v2/buildings/{id}/units com include[]=not_available e os
  mesmos status do orulo_login, num pedido só (a rota ignora `page`). O formato da unidade é o mesmo do portal (mapeamento.py não muda).
- Tabela de preços (arquivos) não existe aqui: a API só entrega arquivo com
  login de corretor (oruloEndUserAuth) e proíbe gravar. A etapa das tabelas
  continua só no orulo_login.

O id do empreendimento é o mesmo do portal; o banco casa as duas fontes pelo
mesmo espaço de ids (migration fonte_orulo_api).
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


USER_AGENT_API = "bkm-monitor-mercado/1.0 (+integracao Orulo IM)"
POR_PAGINA = 50
MAX_PAGINAS = 400


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
        self.http.http.headers["User-Agent"] = USER_AGENT_API
        self.disclaimer: str | None = None
        self.limite_excedido: list[str] = []

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
        saida: list[dict] = []
        vistos: set[str] = set()
        pagina = 1
        total = 1
        while pagina <= min(total, MAX_PAGINAS):
            data = self._json(
                "GET", f"{BASE}/api/v2/buildings",
                params=[
                    ("state", uf),
                    ("city", cidade),
                    ("status[]", "ready"),
                    ("status[]", "under_construction"),
                    ("finality[]", "residential"),
                    ("page", str(pagina)),
                    ("results_per_page", str(POR_PAGINA)),
                ],
            )
            if not isinstance(data, dict):
                break
            if data.get("disclaimer") and not self.disclaimer:
                self.disclaimer = str(data.get("disclaimer"))
            if data.get("results_limit_exceeded") and cidade not in self.limite_excedido:
                self.limite_excedido.append(cidade)
            lote = data.get("buildings") or []
            for b in lote:
                if isinstance(b, dict) and b.get("id") is not None and str(b["id"]) not in vistos:
                    vistos.add(str(b["id"]))
                    saida.append(b)
            total = int(data.get("total_pages") or 1)
            if not lote:
                break
            pagina += 1
        return saida

    def detalhe(self, building_id):
        return self._json(
            "GET", f"{BASE}/api/v2/buildings/{building_id}",
            params=[("include[]", "not_available")],
        )

    def unidades(self, building_id) -> list | None:
        # A rota devolve todas as unidades de uma vez e ignora `page` (conferido em
        # 26/nov/2026: page=1, 2 e 3 trazem as mesmas 346 unidades). Um pedido só.
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
                    if mun.nome in self.limite_excedido:
                        forcar = True
                        falhas.append({
                            "id_externo": "", "motivo": "descoberta",
                            "detalhe": f"{mun.nome}: listagem passou de 10 mil (results_limit_exceeded)",
                        })
                    for bruto in lista:
                        visitar(bruto.get("id"), bruto)
                        if forcar or falhou:
                            break
        finally:
            self.fechar()
        return ResultadoFonte(
            empreendimentos=emps,
            bruto={"fonte": self.nome, "tipo": ctx.tipo, "referencia": ctx.referencia.isoformat(),
                   "disclaimer": self.disclaimer, "itens": itens},
            esperados=esperados,
            falhas=falhas,
            forcar_incompleta=forcar,
            falhou=falhou,
            motivo_falha=motivo,
            avisos=([f"Órulo: {self.disclaimer}"] if self.disclaimer else []),
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
