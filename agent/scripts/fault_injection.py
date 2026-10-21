#!/usr/bin/env python3
"""Fault injection: measure how many deliberately planted bugs the agent catches (DECISIONS.md D16).

For each fault, a temporary COPY of the DemoBank backend is patched with exactly one bug and
started on port 8001, with a second frontend dev server on 5174 pointing at it. The agent then
runs against that faulty stack for the requirement(s) the fault should break. The kit source and
the normal servers (5173/8000) are never modified.

Usage (from agent/, with the agent venv and GEMINI_API_KEY loaded, e.g. via direnv):
  python scripts/fault_injection.py --list
  python scripts/fault_injection.py --fault same_account_allowed --only TRF-02 \
      --requirements runs/<run>/requirements.json
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent
KIT = AGENT.parent / "test-website"
BACKEND_PORT, FRONTEND_PORT = 8001, 5174

# name -> (file, original text, replacement, what the agent should notice)
FAULTS: dict[str, tuple[str, str, str, str]] = {
    "same_account_allowed": (
        "main.py",
        (
            "        if payload.source_account_id == payload.destination_account_id:\n"
            '            raise ApiError(422, "same_account", "Source and destination accounts must be different.")\n'
        ),
        "",
        "Same-account transfers are no longer rejected.",
    ),
    "credit_short_by_one_cent": (
        "repository.py",
        '"UPDATE accounts SET balance = balance + %s WHERE id = %s",\n                (amount, destination_account_id),',
        (
            '"UPDATE accounts SET balance = balance + %s WHERE id = %s",\n'
            '                (amount - Decimal("0.01"), destination_account_id),'
        ),
        "The destination is credited one cent less than the source is debited (atomicity broken).",
    ),
    "status_filter_ignored": (
        "repository.py",
        '        if status is not None:\n            clauses.append("t.status = %s")\n            params.append(status)\n',
        "",
        "The transaction status filter is silently ignored.",
    ),
    "email_unmasked": (
        "repository.py",
        'return Customer(id=row["id"], display_name=row["display_name"], masked_email=row["masked_email"])',
        'return Customer(id=row["id"], display_name=row["display_name"], masked_email="avery.demo@example.test")',
        "The profile shows the full, unmasked email address.",
    ),
    "balance_one_decimal": (
        "schemas.py",
        'return f"{value:.2f}"',
        'return f"{value:.1f}"',
        "Balances and amounts are formatted with one decimal place instead of two.",
    ),
}


def wait_for(url: str, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not come up within {timeout}s")


def start_faulty_stack(fault: str, workdir: Path) -> list[subprocess.Popen]:
    filename, original, replacement, _ = FAULTS[fault]
    pkg = workdir / "demobank"
    shutil.copytree(KIT / "backend" / "demobank", pkg)
    target = pkg / filename
    source = target.read_text()
    if original not in source:
        raise RuntimeError(f"fault {fault}: original text not found in {filename} (kit changed?)")
    target.write_text(source.replace(original, replacement, 1))
    main = pkg / "main.py"
    main.write_text(main.read_text().replace(
        'allow_origins=["http://localhost:5173"]',
        f'allow_origins=["http://localhost:5173", "http://localhost:{FRONTEND_PORT}"]',
    ))

    env = os.environ | {
        "DATABASE_URL": os.environ.get(
            "DEMOBANK_DATABASE_URL", "postgresql://demobank:demobank_local@localhost:5432/demobank"
        ),
        "SESSION_SECRET": secrets.token_urlsafe(32),
        "PYTHONPATH": str(workdir),
    }
    uvicorn = KIT / "backend" / ".venv" / "bin" / "uvicorn"
    backend = subprocess.Popen(
        [str(uvicorn), "demobank.main:app", "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
        cwd=workdir, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    frontend = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(FRONTEND_PORT)],
        cwd=KIT / "frontend", env=os.environ | {"VITE_API_BASE_URL": f"http://localhost:{BACKEND_PORT}"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    wait_for(f"http://localhost:{BACKEND_PORT}/health")
    wait_for(f"http://localhost:{FRONTEND_PORT}/")
    return [backend, frontend]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--fault", choices=sorted(FAULTS))
    parser.add_argument("--only", help="requirement ids the fault should break")
    parser.add_argument("--requirements", help="requirements.json from a baseline run (keeps ids stable)")
    args = parser.parse_args()
    if args.list or not args.fault:
        for name, (_f, _o, _r, effect) in FAULTS.items():
            print(f"{name:26s} {effect}")
        return 0

    workdir = Path(tempfile.mkdtemp(prefix=f"fault-{args.fault}-"))
    procs: list[subprocess.Popen] = []
    try:
        print(f"[fault] starting faulty stack: {args.fault}: {FAULTS[args.fault][3]}")
        procs = start_faulty_stack(args.fault, workdir)
        cmd = [str(AGENT / ".venv" / "bin" / "qa-agent"), "run", "--prd", str(KIT / "PRD.md"),
               "--url", f"http://localhost:{FRONTEND_PORT}"]
        if args.only:
            cmd += ["--only", args.only]
        if args.requirements:
            cmd += ["--requirements", args.requirements]
        env = os.environ | {"QA_RUNS_DIR": str(AGENT / "runs" / "faults" / args.fault)}
        return subprocess.call(cmd, cwd=AGENT, env=env)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(workdir, ignore_errors=True)
        print("[fault] faulty stack stopped and removed")


if __name__ == "__main__":
    sys.exit(main())
