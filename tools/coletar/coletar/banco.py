"""Banco da rodada.

Produção fala com o Supabase pela API (service role), no schema `public`:
RPC `im_*` e o bruto no bucket privado `im-bruto`. O teste ponta a ponta usa
o Postgres local e as mesmas funções, sem PostgREST e sem projeto remoto.

Não há empresa: o monitor é da BKM inteira.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import date
from pathlib import Path

import httpx

from coletar.erros import ColetaErro

try:
    from psycopg.types.json import Jsonb
except ImportError:  # produção não instala o driver; só o teste local
    Jsonb = None

BUCKET = "im-bruto"

# im_fechar_coleta tem statement_timeout próprio de 10 min (migration fechar_timeout).
# O cliente espera mais que isso. O gateway da API corta a requisição em ~125 s com
# 504, mas o banco continua; aí o coletor espera a coleta sair de 'rodando'.
FECHAR_LEITURA_S = 660.0
FECHAR_ESPERA_S = 720.0
FECHAR_INTERVALO_S = 15.0
_GATEWAY = (502, 503, 504)


def _py(valor):
    if isinstance(valor, uuid.UUID):
        return str(valor)
    if isinstance(valor, str):
        texto = valor.strip()
        if texto.startswith("{") or texto.startswith("[") or texto.startswith('"'):
            try:
                return json.loads(texto)
            except json.JSONDecodeError:
                return valor
    return valor


class BancoSupabase:
    def __init__(self, url: str, chave: str, transporte: httpx.BaseTransport | None = None, *,
                 sleep=time.sleep, relogio=time.monotonic):
        if not url or not chave:
            raise ColetaErro("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY são obrigatórios")
        self.url = url.rstrip("/")
        self.chave = chave
        self.http = httpx.Client(transport=transporte, timeout=120.0)
        self._sleep = sleep
        self._relogio = relogio

    def fechar(self) -> None:
        self.http.close()

    def _cabecalhos(self) -> dict:
        return {
            "apikey": self.chave,
            "Authorization": f"Bearer {self.chave}",
            "Content-Type": "application/json",
        }

    def _rpc(self, nome: str, corpo: dict):
        r = self.http.post(f"{self.url}/rest/v1/rpc/{nome}", headers=self._cabecalhos(), json=corpo)
        if r.status_code >= 400:
            raise ColetaErro(f"{nome} retornou {r.status_code}: {r.text[:300]}")
        if not r.text or r.text == "null":
            return None
        return r.json()

    def _get(self, tabela: str, params: dict):
        r = self.http.get(
            f"{self.url}/rest/v1/{tabela}",
            headers=self._cabecalhos(),
            params=params,
        )
        if r.status_code >= 400:
            raise ColetaErro(f"leitura de {tabela} retornou {r.status_code}: {r.text[:300]}")
        return r.json()

    def abrir(self, fonte: str, tipo: str, referencia: date, caminho: str) -> str:
        coleta = self._rpc("im_abrir_coleta", {
            "p_fonte": fonte,
            "p_tipo": tipo,
            "p_referencia": referencia.isoformat(),
            "p_esperados": None,
            "p_bruto_caminho": caminho,
        })
        return str(coleta).strip('"')

    def importar(self, coleta_id: str, empreendimentos: list[dict]) -> dict:
        return self._rpc("im_importar", {"p_coleta": coleta_id, "p_dados": empreendimentos}) or {}

    def fechar_coleta(self, coleta_id: str, esperados: int | None, *, forcar_incompleta: bool = False) -> dict:
        """im_fechar_coleta. Se a resposta se perde (504 do gateway, leitura estourada,
        conexão caída), o fechamento pode seguir no banco: espera a coleta sair de
        'rodando' e devolve o que ficou gravado. Não chama im_falhar_coleta nesse caso."""
        corpo = {"p_coleta": coleta_id, "p_esperados": esperados, "p_forcar_incompleta": forcar_incompleta}
        try:
            r = self.http.post(
                f"{self.url}/rest/v1/rpc/im_fechar_coleta", headers=self._cabecalhos(), json=corpo,
                timeout=httpx.Timeout(30.0, read=FECHAR_LEITURA_S),
            )
        except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError) as e:
            motivo = type(e).__name__
        else:
            if r.status_code not in _GATEWAY:
                if r.status_code >= 400:
                    raise ColetaErro(f"im_fechar_coleta retornou {r.status_code}: {r.text[:300]}")
                if not r.text or r.text == "null":
                    return {}
                return r.json()
            motivo = f"HTTP {r.status_code}"
        return self._esperar_fechamento(coleta_id, motivo)

    def _esperar_fechamento(self, coleta_id: str, motivo: str) -> dict:
        import logging
        log = logging.getLogger("coletar")
        log.warning("fechamento sem resposta (%s); esperando a coleta %s sair de 'rodando'", motivo, coleta_id)
        limite = self._relogio() + FECHAR_ESPERA_S
        while True:
            try:
                linhas = self._get("im_coletas", {
                    "select": "situacao,coletados,unidades,esperados",
                    "id": f"eq.{coleta_id}",
                })
            except (httpx.HTTPError, ColetaErro) as e:
                log.warning("leitura da coleta falhou (%s); tentando de novo", type(e).__name__)
                linhas = None
            if linhas and linhas[0].get("situacao") != "rodando":
                col = linhas[0]
                return {
                    "situacao": col.get("situacao"),
                    "coletados": col.get("coletados"),
                    "unidades": col.get("unidades"),
                    "esperados": col.get("esperados"),
                    "vendas": self._contar("im_vendas", {"coleta_id": f"eq.{coleta_id}"}),
                    "removidas": self._contar("im_unidades", {"removida_coleta_id": f"eq.{coleta_id}"}),
                    "aguardado": True,
                }
            if self._relogio() >= limite:
                raise ColetaErro(
                    f"im_fechar_coleta sem resposta ({motivo}) e a coleta {coleta_id} "
                    f"continua 'rodando' depois de {FECHAR_ESPERA_S:.0f} s"
                ) from None
            self._sleep(FECHAR_INTERVALO_S)

    def _contar(self, tabela: str, filtro: dict) -> int | None:
        try:
            r = self.http.get(
                f"{self.url}/rest/v1/{tabela}",
                headers={**self._cabecalhos(), "Prefer": "count=exact", "Range": "0-0"},
                params={"select": "id", **filtro},
            )
            fim = r.headers.get("content-range", "").rsplit("/", 1)[-1]
            return int(fim) if r.status_code < 400 and fim.isdigit() else None
        except httpx.HTTPError:
            return None

    def agrupar(self) -> dict:
        """im_agrupar: põe em grupo (resumo do empreendimento) as unidades que ainda não têm.
        Tem statement_timeout próprio de 10 min; o gateway pode cortar antes, mas o banco segue."""
        r = self.http.post(f"{self.url}/rest/v1/rpc/im_agrupar", headers=self._cabecalhos(), json={},
                           timeout=httpx.Timeout(30.0, read=FECHAR_LEITURA_S))
        if r.status_code >= 400:
            raise ColetaErro(f"im_agrupar retornou {r.status_code}: {r.text[:300]}")
        return r.json() if r.text and r.text != "null" else {}

    # Tabelas de preço (categoria social): migration categoria_social.
    def tabelas_pendentes(self, municipios: list[str] | None, limite: int | None = None) -> list[dict]:
        return self._rpc("im_tabelas_pendentes", {"p_municipios": municipios, "p_limite": limite}) or []

    def unidades_para_tabela(self, empreendimento_id: str) -> list[dict]:
        return self._rpc("im_unidades_para_tabela", {"p_emp": empreendimento_id}) or []

    def registrar_tabela_preco(self, dados: dict) -> dict:
        return self._rpc("im_registrar_tabela_preco", {"p": dados}) or {}

    def anotar_falha(self, coleta_id: str, id_externo: str | None, motivo: str, detalhe: str) -> None:
        self._rpc("im_anotar_falha", {
            "p_coleta": coleta_id,
            "p_id": id_externo or "",
            "p_motivo": motivo,
            "p_detalhe": (detalhe or "")[:500],
        })

    def falhar(self, coleta_id: str, motivo: str) -> None:
        self._rpc("im_falhar_coleta", {"p_coleta": coleta_id, "p_motivo": (motivo or "")[:500]})

    def conhecidos(self, fonte: str) -> dict[str, str]:
        ids = self._get("im_ids_externos", {
            "select": "id_externo,entidade_id",
            "fonte": f"eq.{fonte}",
            "entidade": "eq.empreendimento",
        })
        if not ids:
            return {}
        situacao = {}
        ents = [row["entidade_id"] for row in ids if row.get("entidade_id")]
        for i in range(0, len(ents), 80):
            lote = ",".join(ents[i:i + 80])
            estados = self._get("im_empreendimento_estados", {
                "select": "empreendimento_id,situacao",
                "ate_coleta_id": "is.null",
                "empreendimento_id": f"in.({lote})",
            })
            for estado in estados:
                situacao[estado["empreendimento_id"]] = estado["situacao"]
        return {
            row["id_externo"]: situacao[row["entidade_id"]]
            for row in ids
            if row.get("id_externo") and row.get("entidade_id") in situacao
        }

    def gravar_bruto(self, caminho: str, conteudo: bytes) -> None:
        r = self.http.post(
            f"{self.url}/storage/v1/object/{BUCKET}/{caminho}",
            headers={
                "apikey": self.chave,
                "Authorization": f"Bearer {self.chave}",
                "Content-Type": "application/gzip",
                "x-upsert": "true",
            },
            content=conteudo,
        )
        if r.status_code >= 400:
            raise ColetaErro(f"storage retornou {r.status_code}: {r.text[:300]}")


class BancoPostgres:
    """As mesmas funções `im_*` do schema public, num Postgres local.

    O bruto vai para a pasta e para storage.objects, no bucket im-bruto.
    """

    def __init__(self, dsn: str, pasta: Path):
        if Jsonb is None:
            raise ColetaErro("o banco local precisa de psycopg")
        import psycopg
        self.pasta = Path(pasta)
        self.conn = psycopg.connect(dsn)
        self.conn.autocommit = True
        self.conn.execute("select set_config('request.jwt.claim.role', 'service_role', false)")

    def fechar(self) -> None:
        self.conn.close()

    def abrir(self, fonte: str, tipo: str, referencia: date, caminho: str) -> str:
        row = self.conn.execute(
            "select public.im_abrir_coleta(%s, %s, %s::date, %s::int, %s)",
            (fonte, tipo, referencia, None, caminho),
        ).fetchone()
        return str(row[0])

    def importar(self, coleta_id: str, empreendimentos: list[dict]) -> dict:
        row = self.conn.execute(
            "select public.im_importar(%s::uuid, %s::jsonb)",
            (coleta_id, Jsonb(empreendimentos)),
        ).fetchone()
        return _py(row[0]) or {}

    def fechar_coleta(self, coleta_id: str, esperados: int | None, *, forcar_incompleta: bool = False) -> dict:
        row = self.conn.execute(
            "select public.im_fechar_coleta(%s::uuid, %s::int, %s)",
            (coleta_id, esperados, forcar_incompleta),
        ).fetchone()
        return _py(row[0]) or {}

    def agrupar(self) -> dict:
        row = self.conn.execute("select public.im_agrupar()").fetchone()
        return _py(row[0]) or {}

    def tabelas_pendentes(self, municipios: list[str] | None, limite: int | None = None) -> list[dict]:
        row = self.conn.execute(
            "select public.im_tabelas_pendentes(%s::text[], 25, %s::int)", (municipios, limite),
        ).fetchone()
        return _py(row[0]) or []

    def unidades_para_tabela(self, empreendimento_id: str) -> list[dict]:
        row = self.conn.execute(
            "select public.im_unidades_para_tabela(%s::uuid)", (empreendimento_id,),
        ).fetchone()
        return _py(row[0]) or []

    def registrar_tabela_preco(self, dados: dict) -> dict:
        row = self.conn.execute(
            "select public.im_registrar_tabela_preco(%s::jsonb)", (Jsonb(dados),),
        ).fetchone()
        return _py(row[0]) or {}

    def anotar_falha(self, coleta_id: str, id_externo: str | None, motivo: str, detalhe: str) -> None:
        self.conn.execute(
            "select public.im_anotar_falha(%s::uuid, %s, %s, %s)",
            (coleta_id, id_externo or "", motivo, (detalhe or "")[:500]),
        )

    def falhar(self, coleta_id: str, motivo: str) -> None:
        self.conn.execute(
            "select public.im_falhar_coleta(%s::uuid, %s)",
            (coleta_id, (motivo or "")[:500]),
        )

    def conhecidos(self, fonte: str) -> dict[str, str]:
        rows = self.conn.execute(
            """
            select i.id_externo, s.situacao
              from public.im_ids_externos i
              join public.im_empreendimento_estados s
                on s.empreendimento_id = i.entidade_id and s.ate_coleta_id is null
             where i.fonte = %s and i.entidade = 'empreendimento'
            """,
            (fonte,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def gravar_bruto(self, caminho: str, conteudo: bytes) -> None:
        dest = self.pasta / caminho
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(conteudo)
        self.conn.execute(
            "delete from storage.objects where bucket_id = %s and name = %s",
            (BUCKET, caminho),
        )
        self.conn.execute(
            "insert into storage.objects (bucket_id, name) values (%s, %s)",
            (BUCKET, caminho),
        )
