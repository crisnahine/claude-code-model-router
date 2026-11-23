"""Pass when DESIGN.json retries the charge once in the payments client with one key per checkout, gateway retries
off, a per-try timeout of at least p99 and a backoff of at least 50 ms that together fit the browser's deadline."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("passcheck", Path(__file__).resolve().parent.parent / "passcheck.py")
passcheck = importlib.util.module_from_spec(_spec)
sys.modules["passcheck"] = passcheck
_spec.loader.exec_module(passcheck)

d = passcheck.answer(Path(sys.argv[1]), "DESIGN.json")
attempts, timeout, backoff = (d.get(k) for k in ("attempts", "per_try_timeout_ms", "backoff_ms"))
ok = (set(d) == {"retry_at", "attempts", "per_try_timeout_ms", "backoff_ms", "idempotency_key", "gateway_retries",
                 "reasons"}
      and d["retry_at"] == "payments_client" and d["idempotency_key"] == "per_checkout"
      and d["gateway_retries"] == "off" and all(type(n) is int for n in (attempts, timeout, backoff))
      and attempts == 2 and timeout >= 600 and backoff >= 50
      and 200 + attempts * timeout + (attempts - 1) * backoff + 100 <= 3000 and passcheck.reasons(d))
sys.exit(0 if ok else 1)
