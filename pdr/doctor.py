"""Preflight (D-32): python, dependencies, settings, data dir, keys (names only), optional reachability."""
from __future__ import annotations

import importlib
import os
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

DEPS = ["pydantic", "httpx", "yaml", "typer", "trafilatura", "pypdf", "openpyxl", "jinja2"]
REQUIRED_KEYS = ["CRUSTDATA_API_KEY"]
OPTIONAL_KEYS = ["ANTHROPIC_API_KEY"]  # model calls degrade to "did not finish" without it
DEFAULT_ENV_FILES = [Path(".env"), Path("/projects/2022B4AA1300P/hackathon/harsh/.env")]


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str = ""


def env_files() -> list[Path]:
    env = os.environ.get("PDR_ENV_FILE")
    return [Path(env)] if env else DEFAULT_ENV_FILES


def key_present(name: str) -> tuple[bool, str]:
    """Whether a key is set in the environment or an env file. Never reads out or returns the value."""
    if os.environ.get(name, "").strip():
        return True, "environment"
    for f in env_files():
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                k, sep, v = line.strip().partition("=")
                if sep and k.strip().removeprefix("export ").strip() == name and v.strip().strip("'\""):
                    return True, str(f)
        except OSError:
            continue
    return False, ""


def run_checks(settings_path: str | None = None, data_dir: str | None = None, online: bool = False) -> list[Check]:
    out: list[Check] = []
    v = sys.version_info
    out.append(Check("python", "ok" if v >= (3, 11) else "fail", f"{v.major}.{v.minor}.{v.micro} (need 3.11+)"))
    missing = []
    for mod in DEPS:
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod)
    out.append(Check("dependencies", "fail" if missing else "ok",
                     f"missing: {', '.join(missing)} (pip install -r requirements.txt)" if missing else f"{len(DEPS)} import"))
    settings = None
    try:
        from pdr.settings import load_for_start
        settings = load_for_start(settings_path)
        out.append(Check("settings", "ok", f"version {settings.version}"))
    except Exception as e:  # noqa: BLE001
        out.append(Check("settings", "fail", str(e)))
    d = Path(data_dir or os.environ.get("PDR_DATA_DIR") or "data")
    try:
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=d, prefix=".doctor"):
            pass
        out.append(Check("data dir", "ok", f"{d.resolve()} writable"))
    except OSError as e:
        out.append(Check("data dir", "fail", f"{d}: not writable ({e.strerror})"))
    where = ", ".join(str(f) for f in env_files())
    for k in REQUIRED_KEYS:
        ok, src = key_present(k)
        out.append(Check(f"key {k}", "ok" if ok else "fail",
                         f"set ({src})" if ok else f"not set in the environment or {where}; paid evidence calls cannot run"))
    for k in OPTIONAL_KEYS:
        ok, src = key_present(k)
        out.append(Check(f"key {k}", "ok" if ok else "warn",
                         f"set ({src})" if ok else "not set: model reads will not finish (verdicts stay cannot_verify)"))
    if settings is not None:
        out.extend(llm_checks(settings))
    if online:
        url = settings.providers.crustdata.base_url if settings else "https://api.crustdata.com"
        host = urlparse(url).hostname or ""
        try:  # a TCP connect only: no request, no payload, no cost
            socket.create_connection((host, 443), timeout=5).close()
            out.append(Check("crustdata reachable", "ok", f"{host}:443"))
        except OSError as e:
            out.append(Check("crustdata reachable", "fail", f"{host}:443 unreachable ({e}); free checks still run"))
    return out


def llm_checks(settings) -> list[Check]:
    """Active model provider, model ids, key env var (name only, never the value), prices set or not."""
    from pdr.broker.pricing import prices_set
    from pdr.llm_openai import key_env
    cfg = settings.providers.llm
    provider = (getattr(cfg, "provider", "") or "anthropic").strip()
    models = [cfg.model_extract, cfg.model_parse]
    detail = f"{provider}; models extract={cfg.model_extract} parse={cfg.model_parse}"
    if provider == "openai_compatible":
        detail += f"; base_url={getattr(cfg, 'base_url', '') or '(not set)'}"
    name = key_env(cfg)
    ok, src = key_present(name)
    priced = prices_set(settings, models)
    return [Check("llm provider", "ok" if provider in ("anthropic", "openai_compatible") else "fail", detail),
            Check(f"llm key {name}", "ok" if ok else "warn",
                  f"set ({src})" if ok else "not set: model reads will not finish"),
            Check("llm prices", "ok" if priced else "warn",
                  "set" if priced else "not set (0): model calls show tokens, cost 'unpriced'; "
                  "set providers.llm.prices from the provider's price page")]


def failed(checks: list[Check]) -> bool:
    return any(c.status == "fail" for c in checks)
