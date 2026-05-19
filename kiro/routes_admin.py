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
    models = _model_snapshots(manager)
    total_requests = sum(account["stats"]["total_requests"] for account in accounts)
    successful_requests = sum(account["stats"]["successful_requests"] for account in accounts)
    failed_requests = sum(account["stats"]["failed_requests"] for account in accounts)

    return {
        "version": APP_VERSION,
        "generated_at": time.time(),
        "account_system": bool(getattr(request.app.state, "account_system", False)),
        "prompt_filter_mode": PROMPT_FILTER_MODE,
        "accounts_total": len(accounts),
        "accounts_available": sum(1 for account in accounts if account["available"]),
        "accounts_cooling": sum(1 for account in accounts if account["status"] == "cooling"),
        "accounts_initialized": sum(1 for account in accounts if account["initialized"]),
        "total_requests": total_requests,
        "successful_requests": successful_requests,
        "failed_requests": failed_requests,
        "success_rate": round((successful_requests / total_requests) * 100, 1) if total_requests else 0.0,
        "models_mapped": len(models),
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


@router.get("/api/models", dependencies=[Security(verify_admin_api_key)])
async def admin_models(request: Request) -> List[Dict[str, Any]]:
    return _model_snapshots(request.app.state.account_manager)


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
        total_requests = getattr(stats, "total_requests", 0)
        successful_requests = getattr(stats, "successful_requests", 0)
        initialized = getattr(account, "auth_manager", None) is not None
        available = cooldown_seconds <= 0
        snapshots.append({
            "id": account_id,
            "label": Path(account_id).name or account_id,
            "initialized": initialized,
            "available": available,
            "status": "cooling" if cooldown_seconds > 0 else ("ready" if initialized else "idle"),
            "failures": getattr(account, "failures", 0),
            "cooldown_seconds_remaining": cooldown_seconds,
            "models_cached_at": getattr(account, "models_cached_at", 0.0),
            "models_count": model_count,
            "stats": {
                "total_requests": total_requests,
                "successful_requests": successful_requests,
                "failed_requests": getattr(stats, "failed_requests", 0),
                "success_rate": round((successful_requests / total_requests) * 100, 1) if total_requests else 0.0,
            },
        })

    return sorted(snapshots, key=lambda item: item["label"])


def _model_snapshots(manager: Any) -> List[Dict[str, Any]]:
    model_to_accounts = getattr(manager, "_model_to_accounts", {})
    accounts = getattr(manager, "_accounts", {})
    models: List[Dict[str, Any]] = []

    for model, model_accounts in model_to_accounts.items():
        account_ids = list(getattr(model_accounts, "accounts", []))
        labels = [Path(account_id).name or account_id for account_id in account_ids if account_id in accounts]
        models.append({
            "model": model,
            "accounts_count": len(labels),
            "accounts": labels,
        })

    return sorted(models, key=lambda item: item["model"])


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
    :root {
      color-scheme: light;
      --bg: #f4f7fb;
      --surface: #ffffff;
      --surface-2: #eef4f7;
      --text: #14212b;
      --muted: #667785;
      --line: #d8e3ea;
      --accent: #126a72;
      --accent-2: #315caa;
      --good: #16805a;
      --warn: #a45b11;
      --bad: #b42318;
      --shadow: 0 16px 36px rgba(31, 53, 72, 0.10);
    }
    :root[data-theme="dark"] {
      color-scheme: dark;
      --bg: #11161c;
      --surface: #18212a;
      --surface-2: #202c36;
      --text: #edf4f8;
      --muted: #9badba;
      --line: #31424f;
      --accent: #4db6ac;
      --accent-2: #8ba8ff;
      --good: #58c896;
      --warn: #f5b86b;
      --bad: #ff8a80;
      --shadow: 0 18px 42px rgba(0, 0, 0, 0.35);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, color-mix(in srgb, var(--accent) 18%, transparent), transparent 34rem),
        linear-gradient(180deg, var(--bg), color-mix(in srgb, var(--bg) 88%, #000 12%));
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 36px; }
    header {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 16px;
      align-items: center;
      margin-bottom: 18px;
    }
    h1 { margin: 0; font-size: 28px; line-height: 1.1; letter-spacing: 0; }
    .subhead { margin-top: 8px; color: var(--muted); font-size: 14px; }
    .toolbar { display: flex; align-items: center; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
    .btn, .seg button {
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      min-height: 38px;
      padding: 8px 12px;
      border-radius: 8px;
      font: inherit;
      font-size: 14px;
      cursor: pointer;
    }
    .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .btn:disabled { opacity: .55; cursor: not-allowed; }
    .auth {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto auto;
      gap: 10px;
      padding: 14px;
      background: color-mix(in srgb, var(--surface) 92%, transparent);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      margin-bottom: 16px;
    }
    input, select {
      width: 100%;
      min-height: 38px;
      padding: 8px 11px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
      font-size: 14px;
    }
    input:focus, select:focus, button:focus-visible {
      outline: 3px solid color-mix(in srgb, var(--accent) 28%, transparent);
      outline-offset: 1px;
    }
    .notice {
      min-height: 20px;
      margin: 0 0 12px;
      color: var(--bad);
      font-size: 13px;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .tile, .panel {
      background: color-mix(in srgb, var(--surface) 96%, transparent);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .tile { padding: 14px; min-height: 104px; }
    .tile-label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .06em; }
    .tile-value { margin-top: 9px; font-size: 28px; line-height: 1; font-weight: 720; letter-spacing: 0; }
    .tile-foot { margin-top: 9px; color: var(--muted); font-size: 12px; }
    .panel { margin-top: 14px; overflow: hidden; }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px;
      border-bottom: 1px solid var(--line);
      background: color-mix(in srgb, var(--surface-2) 72%, transparent);
    }
    .panel-title { font-weight: 680; font-size: 15px; }
    .panel-tools { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .seg {
      display: inline-flex;
      gap: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .seg button { border: 0; border-radius: 0; min-height: 34px; background: transparent; }
    .seg button.active { background: var(--accent); color: #fff; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; min-width: 840px; }
    th, td { padding: 12px 14px; border-bottom: 1px solid var(--line); text-align: left; font-size: 14px; white-space: nowrap; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .06em; background: color-mix(in srgb, var(--surface) 92%, var(--surface-2)); }
    tr:last-child td { border-bottom: 0; }
    .name { font-weight: 650; }
    .muted { color: var(--muted); }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 650;
      border: 1px solid transparent;
    }
    .badge.good { color: var(--good); background: color-mix(in srgb, var(--good) 13%, transparent); border-color: color-mix(in srgb, var(--good) 30%, transparent); }
    .badge.warn { color: var(--warn); background: color-mix(in srgb, var(--warn) 15%, transparent); border-color: color-mix(in srgb, var(--warn) 32%, transparent); }
    .badge.bad { color: var(--bad); background: color-mix(in srgb, var(--bad) 13%, transparent); border-color: color-mix(in srgb, var(--bad) 30%, transparent); }
    .badge.neutral { color: var(--muted); background: var(--surface-2); border-color: var(--line); }
    .progress {
      width: 118px;
      height: 8px;
      overflow: hidden;
      border-radius: 999px;
      background: var(--surface-2);
      border: 1px solid var(--line);
    }
    .bar { height: 100%; background: linear-gradient(90deg, var(--accent), var(--good)); }
    .model-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 10px;
      padding: 14px;
    }
    .model-card {
      min-height: 86px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
    }
    .model-name { font-weight: 650; overflow-wrap: anywhere; }
    .model-meta { margin-top: 8px; color: var(--muted); font-size: 13px; }
    .empty { padding: 24px 14px; color: var(--muted); font-size: 14px; }
    .meta-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      padding: 11px 14px;
      border-top: 1px solid var(--line);
    }
    @media (max-width: 920px) {
      header, .auth { grid-template-columns: 1fr; }
      .toolbar { justify-content: flex-start; }
      .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 560px) {
      main { width: min(100% - 20px, 1180px); padding-top: 18px; }
      .summary { grid-template-columns: 1fr; }
      h1 { font-size: 24px; }
      .tile { min-height: 92px; }
      .panel-head { align-items: stretch; flex-direction: column; }
      .panel-tools { align-items: stretch; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Kiro Gateway Admin</h1>
        <div class="subhead" id="subtitle">Operational dashboard</div>
      </div>
      <div class="toolbar">
        <button class="btn" id="themeButton" type="button" onclick="toggleTheme()">Night</button>
        <button class="btn" id="refreshButton" type="button" onclick="loadAdmin()">Refresh</button>
      </div>
    </header>

    <section class="auth">
      <input id="key" type="password" autocomplete="current-password" placeholder="PROXY_API_KEY">
      <button class="btn primary" id="loadButton" type="button" onclick="loadAdmin()">Load</button>
      <button class="btn" type="button" onclick="clearKey()">Clear</button>
    </section>
    <div id="notice" class="notice"></div>

    <section class="summary" id="summary"></section>

    <section class="panel">
      <div class="panel-head">
        <div class="panel-title">Accounts</div>
        <div class="panel-tools">
          <input id="accountSearch" type="search" placeholder="Search accounts" oninput="renderAccounts()">
          <div class="seg" id="accountFilters">
            <button type="button" class="active" data-filter="all" onclick="setAccountFilter('all')">All</button>
            <button type="button" data-filter="ready" onclick="setAccountFilter('ready')">Ready</button>
            <button type="button" data-filter="cooling" onclick="setAccountFilter('cooling')">Cooling</button>
            <button type="button" data-filter="idle" onclick="setAccountFilter('idle')">Idle</button>
          </div>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Account</th>
              <th>Status</th>
              <th>Requests</th>
              <th>Success</th>
              <th>Failures</th>
              <th>Cooldown</th>
              <th>Models</th>
              <th>Cache</th>
            </tr>
          </thead>
          <tbody id="accounts"></tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <div class="panel-title">Model routing</div>
        <div class="panel-tools">
          <input id="modelSearch" type="search" placeholder="Search models" oninput="renderModels()">
        </div>
      </div>
      <div id="models" class="model-grid"></div>
      <div class="meta-row">
        <span id="version"></span>
        <span id="updated"></span>
      </div>
    </section>
  </main>

  <script>
    const state = { status: null, accounts: [], models: [], filter: 'all' };
    const root = document.documentElement;

    function boot() {
      const theme = localStorage.getItem('kiro-admin-theme') || 'light';
      root.dataset.theme = theme;
      document.getElementById('themeButton').textContent = theme === 'dark' ? 'Light' : 'Night';
      const key = sessionStorage.getItem('kiro-admin-key');
      if (key) document.getElementById('key').value = key;
      renderEmpty();
    }

    function toggleTheme() {
      const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
      root.dataset.theme = next;
      localStorage.setItem('kiro-admin-theme', next);
      document.getElementById('themeButton').textContent = next === 'dark' ? 'Light' : 'Night';
    }

    function clearKey() {
      document.getElementById('key').value = '';
      sessionStorage.removeItem('kiro-admin-key');
    }

    async function api(path) {
      const key = document.getElementById('key').value.trim();
      if (!key) throw new Error('Missing PROXY_API_KEY');
      sessionStorage.setItem('kiro-admin-key', key);
      const res = await fetch(path, { headers: { Authorization: `Bearer ${key}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.json();
    }

    async function loadAdmin() {
      setLoading(true);
      showNotice('');
      try {
        const [status, accounts, models] = await Promise.all([
          api('/admin/api/status'),
          api('/admin/api/accounts'),
          api('/admin/api/models')
        ]);
        state.status = status;
        state.accounts = accounts;
        state.models = models;
        renderAll();
      } catch (err) {
        showNotice(err.message);
      } finally {
        setLoading(false);
      }
    }

    function renderAll() {
      renderSummary();
      renderAccounts();
      renderModels();
      renderMeta();
    }

    function renderEmpty() {
      document.getElementById('summary').innerHTML = [
        tile('Accounts', '--', 'available / total'),
        tile('Requests', '--', 'successful / total'),
        tile('Success rate', '--', 'completed requests'),
        tile('Failures', '--', 'recorded by gateway'),
        tile('Models', '--', 'mapped routes'),
        tile('Prompt filter', '--', 'current mode')
      ].join('');
      document.getElementById('accounts').innerHTML = `<tr><td colspan="8" class="empty">No data loaded</td></tr>`;
      document.getElementById('models').innerHTML = `<div class="empty">No data loaded</div>`;
      document.getElementById('version').textContent = '';
      document.getElementById('updated').textContent = '';
    }

    function renderSummary() {
      const s = state.status;
      document.getElementById('summary').innerHTML = [
        tile('Accounts', `${s.accounts_available}/${s.accounts_total}`, `${s.accounts_initialized} initialized`),
        tile('Requests', `${s.successful_requests}/${s.total_requests}`, `${s.failed_requests} failed`),
        tile('Success rate', `${s.success_rate}%`, 'completed requests'),
        tile('Cooling', s.accounts_cooling, 'accounts in backoff'),
        tile('Models', s.models_mapped, 'mapped routes'),
        tile('Prompt filter', s.prompt_filter_mode, s.account_system ? 'account system on' : 'legacy mode')
      ].join('');
    }

    function renderAccounts() {
      const q = document.getElementById('accountSearch').value.toLowerCase().trim();
      const rows = state.accounts
        .filter(a => state.filter === 'all' || a.status === state.filter)
        .filter(a => !q || a.label.toLowerCase().includes(q) || a.id.toLowerCase().includes(q));
      const body = document.getElementById('accounts');
      if (!rows.length) {
        body.innerHTML = `<tr><td colspan="8" class="empty">No matching accounts</td></tr>`;
        return;
      }
      body.innerHTML = rows.map(a => {
        const total = a.stats.total_requests;
        const success = a.stats.successful_requests;
        const rate = a.stats.success_rate || 0;
        return `<tr>
          <td><div class="name">${escapeHtml(a.label)}</div><div class="muted">${shortId(a.id)}</div></td>
          <td>${statusBadge(a.status)}</td>
          <td>${success}/${total}</td>
          <td><div class="progress" aria-label="${rate}%"><div class="bar" style="width:${clamp(rate)}%"></div></div><div class="muted">${rate}%</div></td>
          <td>${a.failures}</td>
          <td>${formatDuration(a.cooldown_seconds_remaining)}</td>
          <td>${a.models_count}</td>
          <td>${formatTimestamp(a.models_cached_at)}</td>
        </tr>`;
      }).join('');
    }

    function renderModels() {
      const q = document.getElementById('modelSearch').value.toLowerCase().trim();
      const rows = state.models.filter(m => !q || m.model.toLowerCase().includes(q));
      const target = document.getElementById('models');
      if (!rows.length) {
        target.innerHTML = `<div class="empty">No matching models</div>`;
        return;
      }
      target.innerHTML = rows.map(m => `
        <div class="model-card">
          <div class="model-name">${escapeHtml(m.model)}</div>
          <div class="model-meta">${m.accounts_count} account${m.accounts_count === 1 ? '' : 's'}</div>
          <div class="model-meta">${escapeHtml(m.accounts.slice(0, 3).join(', ') || 'uninitialized')}</div>
        </div>
      `).join('');
    }

    function renderMeta() {
      const s = state.status;
      document.getElementById('version').textContent = `Version ${s.version}`;
      document.getElementById('updated').textContent = `Updated ${formatClock(s.generated_at)}`;
      document.getElementById('subtitle').textContent = `${s.accounts_available} available accounts`;
    }

    function setAccountFilter(filter) {
      state.filter = filter;
      document.querySelectorAll('#accountFilters button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === filter);
      });
      renderAccounts();
    }

    function tile(label, value, foot) {
      return `<div class="tile"><div class="tile-label">${label}</div><div class="tile-value">${value}</div><div class="tile-foot">${foot}</div></div>`;
    }

    function statusBadge(status) {
      if (status === 'ready') return `<span class="badge good">Ready</span>`;
      if (status === 'cooling') return `<span class="badge warn">Cooling</span>`;
      return `<span class="badge neutral">Idle</span>`;
    }

    function formatDuration(seconds) {
      if (!seconds || seconds <= 0) return '0s';
      if (seconds < 60) return `${seconds}s`;
      if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
      return `${Math.round(seconds / 3600)}h`;
    }

    function formatTimestamp(value) {
      if (!value) return 'never';
      return new Date(value * 1000).toLocaleString();
    }

    function formatClock(value) {
      if (!value) return '--';
      return new Date(value * 1000).toLocaleTimeString();
    }

    function shortId(value) {
      const text = String(value || '');
      return text.length > 44 ? `...${text.slice(-41)}` : text;
    }

    function clamp(value) {
      return Math.max(0, Math.min(100, Number(value) || 0));
    }

    function escapeHtml(value) {
      return String(value || '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[ch]));
    }

    function showNotice(message) {
      document.getElementById('notice').textContent = message;
    }

    function setLoading(loading) {
      document.getElementById('loadButton').disabled = loading;
      document.getElementById('refreshButton').disabled = loading;
    }

    boot();
  </script>
</body>
</html>"""
