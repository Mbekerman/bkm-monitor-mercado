"""Etapa das tabelas de preço: baixa o PDF da tabela do Órulo e grava a categoria social.

Só empreendimentos que ainda não têm tabela processada (im_tabelas_pendentes):
na rodada mensal, isso quer dizer os novos. Um pedido por vez ao Órulo, com a
mesma espera do coletor (ORULO_ATRASO_MIN/MAX). 403/429 ou login recusado param
tudo, sem tentar outro caminho; a próxima rodada retoma de onde parou.

Uso avulso (carga inicial):
  python -m coletar.tabelas --municipio "São Paulo" [--workers 3] [--pasta DIR] [--limite N]
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone

from coletar.erros import ColetaErro, OruloAuthError, OruloBlocked, OruloError
from coletar.tabela_preco import classificar, escolher_tabelas, extrair_linhas, referencia_do_nome

log = logging.getLogger("coletar")

MAX_CANDIDATAS = 2
MAX_ERROS_SEGUIDOS = 8


@dataclass
class ResumoTabelas:
    processados: int = 0
    por_status: Counter = field(default_factory=Counter)
    unidades: Counter = field(default_factory=Counter)
    parou: str | None = None


def ler_tabela(pdf: bytes, unidades: list[dict]) -> dict:
    """Roda no processo de trabalho: texto (ou OCR) + casamento. Só dados simples na volta."""
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    linhas, metodo, paginas = extrair_linhas(pdf)
    r = classificar(linhas, unidades)
    return {
        "status": r.status, "resto_fonte": r.resto_fonte, "metodo": metodo, "paginas": paginas,
        "atribuicoes": r.atribuicoes, "categorias_no_texto": dict(r.categorias_no_texto),
        "ambiguas": r.ambiguas, "conflitos": r.conflitos, "regras_grupo": r.regras_grupo, "nota": r.nota,
    }


class _Sincrono:
    """Executor que roda na hora (job do Cloud Run: 1 CPU, sem processo extra)."""

    def submit(self, fn, *a, **kw) -> Future:
        f: Future = Future()
        try:
            f.set_result(fn(*a, **kw))
        except BaseException as e:  # noqa: BLE001
            f.set_exception(e)
        return f

    def shutdown(self, wait=True):
        pass


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nome_arquivo(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)[:120]


def _gravar(banco, corpo: dict, tentativas: int = 6, espera: float = 30.0) -> dict:
    """Grava com nova tentativa quando o banco está ocupado (lock/statement timeout)."""
    for i in range(tentativas):
        try:
            return banco.registrar_tabela_preco(corpo) or {}
        except ColetaErro as e:
            msg = str(e)
            ocupado = any(k in msg for k in ("55P03", "57014", "lock timeout", "statement timeout", " 502", " 503", " 504"))
            if not ocupado or i == tentativas - 1:
                raise
            log.warning("banco ocupado ao gravar %s (%s); nova tentativa em %.0f s",
                        corpo.get("id_externo"), msg[:80], espera * (i + 1))
            time.sleep(espera * (i + 1))
    return {}


def rodar_tabelas(banco, orulo, municipios: list[str] | None, *, limite: int | None = None,
                  pasta: str | None = None, workers: int = 0, em_voo: int = 4,
                  pendentes: list[dict] | None = None, parar=None) -> ResumoTabelas:
    resumo = ResumoTabelas()
    if pendentes is None:
        pendentes = banco.tabelas_pendentes(municipios, limite)
    log.info("tabelas de preço: %s empreendimentos pendentes", len(pendentes))
    if not pendentes:
        return resumo
    orulo.login()
    fila = deque(pendentes)
    exe = ProcessPoolExecutor(max_workers=workers) if workers > 0 else _Sincrono()
    voo: dict[Future, dict] = {}
    erros_seguidos = 0
    t0 = time.monotonic()

    def registrar(estado: dict, status: str, *, leitura: dict | None = None, detalhe: str = "",
                  resto: str = "fora_da_tabela") -> None:
        nonlocal erros_seguidos
        emp = estado["emp"]
        arq = estado.get("arquivo") or {}
        corpo = {
            "empreendimento_id": emp["empreendimento_id"], "id_externo": str(emp["id_externo"]),
            "arquivo_id": str(arq.get("id") or ""), "arquivo_nome": arq.get("name"),
            "referencia": (referencia_do_nome(arq.get("name") or "") or None),
            "url": estado.get("url"), "baixado_em": estado.get("baixado_em"),
            "status": status, "resto_fonte": resto, "detalhe": detalhe,
        }
        if corpo["referencia"] is not None:
            corpo["referencia"] = corpo["referencia"].isoformat()
        if leitura:
            corpo["metodo_texto"] = leitura["metodo"]
            corpo["paginas"] = leitura["paginas"]
            corpo["unidades"] = [
                {"id": uid, "categoria": c, "fonte": f} for uid, (c, f) in leitura["atribuicoes"].items()
            ]
        volta = _gravar(banco, corpo)
        resumo.processados += 1
        resumo.por_status[status] += 1
        for c, n in (volta.get("categorias") or {}).items():
            resumo.unidades[c] += int(n)
        erros_seguidos = erros_seguidos + 1 if status == "erro" else 0
        extra = ""
        if leitura:
            extra = (f" metodo={leitura['metodo']} pag={leitura['paginas']} amb={leitura['ambiguas']}"
                     f" conf={leitura['conflitos']} regras={leitura['regras_grupo']}"
                     f" texto={leitura['categorias_no_texto']}")
        log.info(
            "tabela %s/%s %s [%s] %s: %s unidades=%s un=%s grupo=%s nota=%s cats=%s%s%s",
            resumo.processados, len(pendentes), emp["id_externo"], (emp.get("nome") or "")[:40],
            arq.get("name") or "-", status, volta.get("unidades"), volta.get("tabela_unidade"),
            volta.get("tabela_grupo"), volta.get("nota_geral"), volta.get("categorias"), extra,
            f" detalhe={detalhe[:160]}" if detalhe else "",
        )
        if resumo.processados % 25 == 0:
            ritmo = (time.monotonic() - t0) / resumo.processados
            log.info("progresso: %s/%s, %s, unidades %s, %.1f s por empreendimento",
                     resumo.processados, len(pendentes), dict(resumo.por_status), dict(resumo.unidades), ritmo)

    def baixar_e_enviar(estado: dict) -> bool:
        """Baixa a candidata da vez e manda ler. False quando acabou sem leitura (já registrou)."""
        emp = estado["emp"]
        bid = emp["id_externo"]
        if "candidatas" not in estado:
            arquivos = orulo.arquivos(bid)
            if arquivos is None:
                registrar(estado, "erro", detalhe="detalhe 404 no Órulo")
                return False
            estado["candidatas"] = escolher_tabelas(arquivos)[:MAX_CANDIDATAS]
            estado["i"] = 0
            if not estado["candidatas"]:
                registrar(estado, "sem_tabela", resto="sem_tabela")
                return False
        if "unidades" not in estado:
            estado["unidades"] = banco.unidades_para_tabela(emp["empreendimento_id"])
        arq = estado["candidatas"][estado["i"]]
        estado["arquivo"] = arq
        url = orulo.url_arquivo(bid, arq.get("id"))
        if not url:
            raise OruloError(f"arquivo {arq.get('id')} sem url")
        estado["url"] = url
        pdf = orulo.baixar(url)
        estado["baixado_em"] = _agora()
        if pasta:
            d = os.path.join(pasta, str(bid))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{arq.get('id')}_{_nome_arquivo(arq.get('name') or 'tabela.pdf')}"), "wb") as fh:
                fh.write(pdf)
        voo[exe.submit(ler_tabela, pdf, estado["unidades"])] = estado
        return True

    try:
        while fila or voo:
            if parar is not None and parar():
                resumo.parou = "pedido de parada"
                fila.clear()
            while fila and len(voo) < max(1, em_voo if workers > 0 else 1):
                estado = {"emp": fila.popleft()}
                try:
                    baixar_e_enviar(estado)
                except (OruloBlocked, OruloAuthError):
                    raise
                except Exception as e:  # noqa: BLE001
                    registrar(estado, "erro", detalhe=f"{type(e).__name__}: {e}")
                if erros_seguidos >= MAX_ERROS_SEGUIDOS:
                    raise ColetaErro(f"{erros_seguidos} erros seguidos; parando")
            if not voo:
                continue
            prontos, _ = wait(list(voo), timeout=600, return_when=FIRST_COMPLETED)
            for fut in prontos:
                estado = voo.pop(fut)
                try:
                    leitura = fut.result()
                except Exception as e:  # noqa: BLE001
                    leitura = None
                    erro = f"leitura: {type(e).__name__}: {e}"
                if leitura is None:
                    registrar(estado, "erro", detalhe=erro)
                    continue
                if leitura["status"] != "ok" and estado["i"] + 1 < len(estado["candidatas"]):
                    estado.setdefault("primeira", (leitura, dict(estado)))
                    estado["i"] += 1
                    try:
                        baixar_e_enviar(estado)
                        continue
                    except (OruloBlocked, OruloAuthError):
                        raise
                    except Exception as e:  # noqa: BLE001
                        log.warning("segunda tabela de %s falhou: %s", estado["emp"]["id_externo"], e)
                if leitura["status"] != "ok" and "primeira" in estado:
                    leitura, antigo = estado["primeira"]
                    for k in ("arquivo", "url", "baixado_em"):
                        estado[k] = antigo.get(k)
                registrar(estado, leitura["status"], leitura=leitura, resto=leitura["resto_fonte"])
    except OruloBlocked as e:
        resumo.parou = f"Órulo bloqueou: {e}"
        log.error("AVISO: tabelas paradas, %s", resumo.parou)
    except OruloAuthError as e:
        resumo.parou = f"login do Órulo: {e}"
        log.error("AVISO: tabelas paradas, %s", resumo.parou)
    except ColetaErro as e:
        resumo.parou = str(e)
        log.error("AVISO: tabelas paradas, %s", e)
    finally:
        for fut in list(voo):
            fut.cancel()
        exe.shutdown(wait=False)
        try:
            orulo.fechar()
        except Exception:  # noqa: BLE001
            pass
    log.info("tabelas de preço: %s processados %s, unidades %s%s", resumo.processados,
             dict(resumo.por_status), dict(resumo.unidades), f", parou: {resumo.parou}" if resumo.parou else "")
    return resumo


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="coletar.tabelas", description="Tabelas de preço do Órulo -> categoria social")
    p.add_argument("--municipio", action="append", default=None)
    p.add_argument("--limite", type=int, default=None)
    p.add_argument("--pasta", default=None, help="guarda os PDFs baixados aqui")
    p.add_argument("--workers", type=int, default=0, help="processos de leitura/OCR (0 = no mesmo processo)")
    p.add_argument("--parar-se-existir", default=None, help="arquivo que, se criado, para a carga com calma")
    args = p.parse_args(argv)
    from coletar.cli import _atraso, criar_banco
    from coletar.orulo_login import OruloLogin
    banco = criar_banco()
    orulo = OruloLogin(email=os.environ.get("ORULO_EMAIL", ""), senha=os.environ.get("ORULO_PASSWORD", ""),
                       atraso=_atraso())
    parar = (lambda: os.path.exists(args.parar_se_existir)) if args.parar_se_existir else None
    try:
        r = rodar_tabelas(banco, orulo, args.municipio, limite=args.limite, pasta=args.pasta,
                          workers=args.workers, parar=parar)
    finally:
        banco.fechar()
    return 0 if not r.parou else 2


if __name__ == "__main__":
    sys.exit(main())
