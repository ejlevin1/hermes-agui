"""Dashboard half of the stand-in host plugin: reports whether `hermes_agui` is importable in the dashboard process."""

from __future__ import annotations

import importlib
import json
import os
import sys

from fastapi import APIRouter

router = APIRouter()


def report() -> dict:
    out: dict = {"python": sys.executable, "version": sys.version.split()[0]}
    for mod in (
        "hermes_agui",
        "hermes_agui_plugin",
        "agui_frontend",
        "hermes_plugins.agui_host_probe.vendor.hermes_agui",
    ):
        try:
            m = importlib.import_module(mod)
            out[mod] = {
                "ok": True,
                "file": getattr(m, "__file__", None),
                "version": getattr(m, "__version__", None),
            }
        except Exception as exc:  # noqa: BLE001 - the probe reports every failure
            out[mod] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    vendor = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor"
    )
    out["vendor_dir_exists"] = os.path.isdir(vendor)
    if (
        os.path.isdir(vendor) and "hermes_agui" not in sys.modules
    ):  # sys.path style vendoring (absolute `hermes_agui` name)
        sys.path.insert(0, vendor)
        try:
            m = importlib.import_module("hermes_agui")
            rel = (
                importlib.import_module("hermes_agui._rel_probe")
                if os.path.exists(os.path.join(vendor, "hermes_agui", "_rel_probe.py"))
                else None
            )
            out["vendor_syspath"] = {
                "ok": True,
                "file": m.__file__,
                "relative_import_ok": rel is not None and rel.OK,
            }
        except Exception as exc:  # noqa: BLE001
            out["vendor_syspath"] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    out["agui_modules"] = sorted(k for k in sys.modules if "agui" in k)
    return out


@router.get("/health")
def health() -> dict:
    return report()


# The dashboard API is behind auth, so the probe also writes its report at import time (= dashboard start).
_path = os.environ.get("AGUI_PROBE_REPORT") or os.path.join(
    os.environ.get("HERMES_HOME", "."), "agui_probe_report.json"
)
with open(_path, "w") as _f:
    json.dump(report(), _f, indent=1)
