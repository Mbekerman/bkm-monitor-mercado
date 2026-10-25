"""Sobe um Postgres 16 temporário com as migrations, no mesmo jeito de supabase/tests/run.sh."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
PGBIN = os.environ.get("PGBIN", "/usr/lib/postgresql/16/bin")


def _porta() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


def _run(cmd: list[str], **kw):
    if os.geteuid() == 0:
        cmd = ["runuser", "-u", "postgres", "--", *cmd]
    return subprocess.run(cmd, check=True, **kw)


class Cluster:
    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="coletar-pg-"))
        self.dir = self.base / "pg"
        self.dir.mkdir()
        self.porta = _porta()
        self.pasta_bruto = self.base / "bruto"
        self.pasta_bruto.mkdir()
        if os.geteuid() == 0:
            shutil.chown(self.base, user="postgres")
            shutil.chown(self.dir, user="postgres")
            shutil.chown(self.pasta_bruto, user="postgres")
        env = os.environ.copy()
        env["PATH"] = f"{PGBIN}:{env.get('PATH', '')}"
        self.env = env
        _run([f"{PGBIN}/initdb", "-D", str(self.dir), "-U", "postgres", "-A", "trust", "-E", "UTF8", "--locale=C"],
             env=env, stdout=subprocess.DEVNULL)
        _run([f"{PGBIN}/pg_ctl", "-D", str(self.dir), "-o", f"-p {self.porta} -k /tmp",
              "-l", str(self.dir / "log"), "start", "-w"], env=env, stdout=subprocess.DEVNULL)
        self.psql = [f"{PGBIN}/psql", "-h", "/tmp", "-p", str(self.porta), "-U", "postgres", "-d", "postgres",
                     "-v", "ON_ERROR_STOP=1", "-q"]
        _run([*self.psql, "-f", str(RAIZ / "supabase/tests/shim_supabase.sql")], env=env, stdout=subprocess.DEVNULL)
        for mig in sorted((RAIZ / "supabase/migrations").glob("*.sql")):
            _run([*self.psql, "-f", str(mig)], env=env, stdout=subprocess.DEVNULL)

    @property
    def dsn(self) -> str:
        return f"host=/tmp port={self.porta} user=postgres dbname=postgres"

    def parar(self) -> None:
        subprocess.run(
            ["runuser", "-u", "postgres", "--", f"{PGBIN}/pg_ctl", "-D", str(self.dir), "-m", "immediate", "stop"]
            if os.geteuid() == 0 else
            [f"{PGBIN}/pg_ctl", "-D", str(self.dir), "-m", "immediate", "stop"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(self.base, ignore_errors=True)
