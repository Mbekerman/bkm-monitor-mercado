"""O job do Cloud Run não passa argumentos. A imagem python traz CMD python3."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ENTRADA = RAIZ / "entrypoint.sh"
DOCKERFILE = RAIZ / "Dockerfile"


def _python_falso(tmp_path, codigo=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    registro = tmp_path / "argv"
    (bindir / "python").write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$REGISTRO\"\n"
        "exit \"$CODIGO\"\n",
        encoding="utf-8",
    )
    (bindir / "python").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["REGISTRO"] = str(registro)
    env["CODIGO"] = str(codigo)
    return env, registro


def test_entrypoint_sem_args_chama_o_modulo(tmp_path):
    env, registro = _python_falso(tmp_path)
    r = subprocess.run([str(ENTRADA)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert registro.read_text(encoding="utf-8").splitlines() == ["-m", "coletar"]


def test_entrypoint_descarta_o_cmd_python3_da_imagem_base(tmp_path):
    env, registro = _python_falso(tmp_path)
    r = subprocess.run([str(ENTRADA), "python3"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert registro.read_text(encoding="utf-8").splitlines() == ["-m", "coletar"]


def test_entrypoint_repassa_id_e_o_codigo_de_saida(tmp_path):
    env, registro = _python_falso(tmp_path, codigo=2)
    r = subprocess.run([str(ENTRADA), "--id", "83029"], env=env, capture_output=True, text=True)
    assert r.returncode == 2
    assert registro.read_text(encoding="utf-8").splitlines() == ["-m", "coletar", "--id", "83029"]


def test_entrypoint_sem_coletar_tipo_sai_1(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
    (bindir / "python").chmod(0o755)
    env = os.environ.copy()
    env.pop("COLETAR_TIPO", None)
    env.pop("COLETAR_PG_DSN", None)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(RAIZ)
    r = subprocess.run([str(ENTRADA)], env=env, capture_output=True, text=True)
    assert r.returncode == 1
    assert "COLETAR_TIPO" in r.stderr
    assert "usage:" not in r.stderr


def test_dockerfile_nao_repassa_o_cmd_python3():
    texto = DOCKERFILE.read_text(encoding="utf-8")
    instrucoes = [
        linha.strip() for linha in texto.splitlines()
        if linha.strip() and not linha.lstrip().startswith("#")
    ]
    assert instrucoes[-1] == 'ENTRYPOINT ["/app/entrypoint.sh"]'
    assert not any(linha.startswith("CMD") for linha in instrucoes)
