# -*- coding: utf-8 -*-

"""
Read-only admin routes for operational visibility.
"""

import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from loguru import logger

from kiro.config import APP_VERSION, PROXY_API_KEY, PROMPT_FILTER_MODE


api_key_header = APIKeyHeader(name="Authorization", auto_error=False)
router = APIRouter(prefix="/admin", tags=["Admin"])


async def verify_admin_api_key(auth_header: str = Security(api_key_header)) -> bool:
    if not auth_header or auth_header != f"Bearer {PROXY_API_KEY}":
        logger.warning("Admin access attempt with invalid API key.")
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return True


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_page() -> str:
    return _ADMIN_HTML


@router.get("/api/status", dependencies=[Security(verify_admin_api_key)])
async def admin_status(request: Request) -> Dict[str, Any]:
    manager = request.app.state.account_manager
    accounts = _account_snapshots(manager)

    return {
        "version": APP_VERSION,
        "account_system": bool(getattr(request.app.state, "account_system", False)),
        "prompt_filter_mode": PROMPT_FILTER_MODE,
        "accounts_total": len(accounts),
        "accounts_available": sum(1 for account in accounts if account["available"]),
        "total_requests": sum(account["stats"]["total_requests"] for account in accounts),
        "successful_requests": sum(account["stats"]["successful_requests"] for account in accounts),
        "failed_requests": sum(account["stats"]["failed_requests"] for account in accounts),
        "models_mapped": len(getattr(manager, "_model_to_accounts", {})),
    }


@router.get("/api/accounts", dependencies=[Security(verify_admin_api_key)])
async def admin_accounts(request: Request) -> List[Dict[str, Any]]:
    return _account_snapshots(request.app.state.account_manager)


@router.get("/api/prompt-filter", dependencies=[Security(verify_admin_api_key)])
async def admin_prompt_filter() -> Dict[str, Any]:
    return {
        "mode": PROMPT_FILTER_MODE,
        "enabled": PROMPT_FILTER_MODE not in ("", "off", "false", "0", "no", "disabled"),
        "available_modes": ["off", "identity"],
    }


def _account_snapshots(manager: Any) -> List[Dict[str, Any]]:
    now = time.time()
    accounts = getattr(manager, "_accounts", {})
    model_to_accounts = getattr(manager, "_model_to_accounts", {})
    snapshots: List[Dict[str, Any]] = []

    for account_id, account in accounts.items():
        cooldown_seconds = _cooldown_seconds_remaining(account, now)
        model_count = sum(
            1 for model_accounts in model_to_accounts.values()
            if account_id in getattr(model_accounts, "accounts", [])
        )
        stats = getattr(account, "stats", None)
        snapshots.append({
            "id": account_id,
            "label": Path(account_id).name or account_id,
            "initialized": getattr(account, "auth_manager", None) is not None,
            "available": cooldown_seconds <= 0,
            "failures": getattr(account, "failures", 0),
            "cooldown_seconds_remaining": cooldown_seconds,
            "models_cached_at": getattr(account, "models_cached_at", 0.0),
            "models_count": model_count,
            "stats": {
                "total_requests": getattr(stats, "total_requests", 0),
                "successful_requests": getattr(stats, "successful_requests", 0),
                "failed_requests": getattr(stats, "failed_requests", 0),
            },
        })

    return snapshots


def _cooldown_seconds_remaining(account: Any, now: float) -> int:
    failures = getattr(account, "failures", 0)
    if failures <= 0:
        return 0

    from kiro.config import ACCOUNT_MAX_BACKOFF_MULTIPLIER, ACCOUNT_RECOVERY_TIMEOUT

    multiplier = min(2 ** (failures - 1), ACCOUNT_MAX_BACKOFF_MULTIPLIER)
    cooldown_until = getattr(account, "last_failure_time", 0.0) + ACCOUNT_RECOVERY_TIMEOUT * multiplier
    return max(0, int(cooldown_until - now))


_ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kiro Gateway Admin</title>
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2933; background: #f7f9fb; }
    main { max-width: 1120px; margin: 0 auto; }
    h1 { font-size: 28px; margin: 0 0 18px; }
    input { width: min(520px, 100%); padding: 10px 12px; border: 1px solid #c7d2da; border-radius: 6px; font: inherit; }
    button { padding: 10px 14px; border: 0; border-radius: 6px; background: #16697a; color: white; font: inherit; cursor: pointer; }
    section { margin-top: 22px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
    .metric, table { background: white; border: 1px solid #dbe4ea; border-radius: 8px; }
    .metric { padding: 14px; }
    .metric strong { display: block; font-size: 24px; margin-top: 6px; }
    table { width: 100%; border-collapse: collapse; overflow: hidden; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #e6edf2; text-align: left; font-size: 14px; }
    th { background: #edf3f6; }
    tr:last-child td { border-bottom: 0; }
    .muted { color: #66788a; }
    .error { color: #b42318; margin-top: 12px; }
  </style>
</head>
<body>
  <main>
    <h1>Kiro Gateway Admin</h1>
    <div>
      <input id="key" type="password" placeholder="PROXY_API_KEY">
      <button onclick="loadAdmin()">Load</button>
    </div>
    <div id="error" class="error"></div>
    <section id="status" class="grid"></section>
    <section>
      <table>
        <thead><tr><th>Account</th><th>Available</th><th>Failures</th><th>Cooldown</th><th>Requests</th><th>Models</th></tr></thead>
        <tbody id="accounts"></tbody>
      </table>
    </section>
  </main>
  <script>
    async function api(path) {
      const key = document.getElementById('key').value;
      const res = await fetch(path, { headers: { Authorization: `Bearer ${key}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.json();
    }
    function metric(label, value) {
      return `<div class="metric"><span class="muted">${label}</span><strong>${value}</strong></div>`;
    }
    async function loadAdmin() {
      document.getElementById('error').textContent = '';
      try {
        const [status, accounts] = await Promise.all([api('/admin/api/status'), api('/admin/api/accounts')]);
        document.getElementById('status').innerHTML = [
          metric('Available accounts', `${status.accounts_available}/${status.accounts_total}`),
          metric('Total requests', status.total_requests),
          metric('Failures', status.failed_requests),
          metric('Prompt filter', status.prompt_filter_mode)
        ].join('');
        document.getElementById('accounts').innerHTML = accounts.map(a => `
          <tr>
            <td>${a.label}</td>
            <td>${a.available ? 'yes' : 'no'}</td>
            <td>${a.failures}</td>
            <td>${a.cooldown_seconds_remaining}s</td>
            <td>${a.stats.successful_requests}/${a.stats.total_requests}</td>
            <td>${a.models_count}</td>
          </tr>
        `).join('');
      } catch (err) {
        document.getElementById('error').textContent = err.message;
      }
    }
  </script>
</body>
</html>"""
