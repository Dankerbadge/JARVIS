from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .approval_inbox import ApprovalInbox
from .runtime import JarvisRuntime


_DASHBOARD_HTML = """<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>JARVIS Operator Surface</title>
  <style>
    :root { color-scheme: light; }
    body { font-family: ui-sans-serif, system-ui; margin: 0; background: #f3f6fb; color: #182231; }
    header { padding: 16px 20px; background: linear-gradient(135deg, #0f172a, #1e3a8a); color: #f8fafc; }
    header h1 { margin: 0; font-size: 20px; }
    header p { margin: 6px 0 0; opacity: .9; }
    main { padding: 18px; display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
    section { background: white; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.08); padding: 12px; }
    section h2 { margin: 0 0 8px; font-size: 15px; }
    pre { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px; font-size: 12px; overflow:auto; max-height: 240px; }
    button { background: #1d4ed8; color: white; border: 0; border-radius: 8px; padding: 8px 10px; cursor: pointer; }
    button.secondary { background: #334155; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
  </style>
</head>
<body>
  <header>
    <h1>JARVIS Operator Surface</h1>
    <p>Home, thoughts, synthesis, identity/context, interrupts, approvals, academics, markets, and daily digest archive.</p>
  </header>
  <main>
    <section>
      <h2>Home</h2>
      <div class=\"row\"><button onclick=\"refreshHome()\">Refresh</button></div>
      <pre id=\"home\">loading...</pre>
    </section>

    <section>
      <h2>Interrupts</h2>
      <div class=\"row\">
        <button onclick=\"refreshInterrupts()\">Refresh</button>
        <button class=\"secondary\" onclick=\"ackFirstInterrupt()\">Ack First</button>
        <button class=\"secondary\" onclick=\"snoozeFirstInterrupt()\">Snooze First (60m)</button>
      </div>
      <pre id=\"interrupts\">loading...</pre>
    </section>

    <section>
      <h2>Approvals</h2>
      <div class=\"row\"><button onclick=\"refreshApprovals()\">Refresh</button></div>
      <pre id=\"approvals\">loading...</pre>
    </section>

    <section>
      <h2>Academics</h2>
      <div class=\"row\"><button onclick=\"refreshAcademics()\">Refresh</button></div>
      <pre id=\"academics\">loading...</pre>
    </section>

    <section>
      <h2>Markets</h2>
      <div class=\"row\"><button onclick=\"refreshMarkets()\">Refresh</button></div>
      <pre id=\"markets\">loading...</pre>
    </section>

    <section>
      <h2>Preferences</h2>
      <div class=\"row\">
        <button onclick=\"setFocus('academics')\">Focus Academics</button>
        <button class=\"secondary\" onclick=\"setFocus('zenith')\">Focus Zenith</button>
        <button class=\"secondary\" onclick=\"setFocus('off')\">Focus Off</button>
      </div>
      <div class=\"row\">
        <button onclick=\"setQuietHours(22,7)\">Quiet 22-07</button>
        <button class=\"secondary\" onclick=\"setQuietHours(null,null)\">Clear Quiet</button>
      </div>
      <div class=\"row\">
        <button onclick=\"suppressForHours(2)\">Suppress 2h</button>
        <button class=\"secondary\" onclick=\"clearSuppress()\">Clear Suppress</button>
      </div>
      <pre id=\"prefs\">loading...</pre>
    </section>

    <section>
      <h2>Daily Digest</h2>
      <div class=\"row\">
        <button onclick=\"refreshDigests()\">Refresh</button>
        <button class=\"secondary\" onclick=\"exportDigest()\">Export Today</button>
      </div>
      <pre id=\"digests\">loading...</pre>
    </section>
  </main>
  <script>
    async function jget(url){ const r = await fetch(url); return await r.json(); }
    async function jpost(url, body){ const r = await fetch(url, {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(body||{})}); return await r.json(); }

    async function refreshHome(){ document.getElementById('home').textContent = JSON.stringify(await jget('/api/home'), null, 2); }
    async function refreshInterrupts(){ const data = await jget('/api/interrupts?status=all&limit=25'); window._interrupts = data.items || []; document.getElementById('interrupts').textContent = JSON.stringify(data, null, 2); }
    async function ackFirstInterrupt(){ if(!window._interrupts || !window._interrupts.length) return; const id = window._interrupts[0].interrupt_id; await jpost(`/api/interrupts/${id}/acknowledge`, {actor:'operator_surface'}); await refreshInterrupts(); }
    async function snoozeFirstInterrupt(){ if(!window._interrupts || !window._interrupts.length) return; const id = window._interrupts[0].interrupt_id; await jpost(`/api/interrupts/${id}/snooze`, {actor:'operator_surface', minutes:60}); await refreshInterrupts(); }
    async function refreshApprovals(){ document.getElementById('approvals').textContent = JSON.stringify(await jget('/api/approvals?status=pending'), null, 2); }
    async function refreshAcademics(){
      const overview = await jget('/api/academics/overview?term_id=current_term');
      const risks = await jget('/api/academics/risks');
      const schedule = await jget('/api/academics/schedule?term_id=current_term');
      const windows = await jget('/api/academics/windows?term_id=current_term');
      document.getElementById('academics').textContent = JSON.stringify({overview, risks, schedule, windows}, null, 2);
    }
    async function refreshMarkets(){
      const overview = await jget('/api/markets/overview?account_id=default');
      const opportunities = await jget('/api/markets/opportunities?limit=25');
      const abstentions = await jget('/api/markets/abstentions?limit=25');
      const handoffs = await jget('/api/markets/handoffs?limit=25');
      const outcomes = await jget('/api/markets/outcomes?limit=25');
      const posture = await jget('/api/markets/posture?account_id=default');
      document.getElementById('markets').textContent = JSON.stringify({overview, opportunities, abstentions, handoffs, outcomes, posture}, null, 2);
    }
    async function refreshPrefs(){ document.getElementById('prefs').textContent = JSON.stringify(await jget('/api/preferences'), null, 2); }
    async function setFocus(domain){ await jpost('/api/preferences/focus-mode', {domain, actor:'operator_surface'}); await refreshPrefs(); }
    async function setQuietHours(startHour, endHour){ await jpost('/api/preferences/quiet-hours', {start_hour:startHour, end_hour:endHour, actor:'operator_surface'}); await refreshPrefs(); }
    async function suppressForHours(hours){ const until = new Date(Date.now() + hours*3600*1000).toISOString(); await jpost('/api/preferences/suppress-until', {until_iso: until, reason: 'operator surface temporary suppression', actor:'operator_surface'}); await refreshPrefs(); }
    async function clearSuppress(){ await jpost('/api/preferences/suppress-until', {until_iso: null, reason: '', actor:'operator_surface'}); await refreshPrefs(); }
    async function refreshDigests(){ document.getElementById('digests').textContent = JSON.stringify(await jget('/api/digests?limit=14'), null, 2); }
    async function exportDigest(){ await jpost('/api/digests/export', {}); await refreshDigests(); }

    (async () => {
      await refreshHome();
      await refreshInterrupts();
      await refreshApprovals();
      await refreshAcademics();
      await refreshMarkets();
      await refreshPrefs();
      await refreshDigests();
    })();
  </script>
</body>
</html>
"""


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, indent=2).encode("utf-8")


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class OperatorHttpServer(HTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        runtime: JarvisRuntime,
    ) -> None:
        self.runtime = runtime
        self.runtime_lock = threading.Lock()
        super().__init__(server_address, OperatorRequestHandler)


class OperatorRequestHandler(BaseHTTPRequestHandler):
    server: OperatorHttpServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # Keep stdout clean for CLI-run server usage.
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, body: str) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    def _route_get(self, path: str, query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        runtime = self.server.runtime
        if path == "/api/health":
            payload = {"status": "ok"}
            payload.update(runtime.get_boot_identity())
            return (200, payload)
        if path == "/api/home":
            return (200, runtime.get_operator_home())
        if path == "/api/thoughts/recent":
            limit = int((query.get("limit") or ["20"])[0])
            items = runtime.list_recent_thoughts(limit=limit)
            return (200, {"count": len(items), "items": items})
        if path.startswith("/api/thoughts/"):
            thought_id = path.split("/")[-1]
            item = runtime.get_thought(thought_id)
            if not item:
                return (404, {"error": "thought_not_found", "thought_id": thought_id})
            return (200, item)
        if path == "/api/cognition/config":
            return (200, runtime.get_cognition_config())
        if path == "/api/synthesis/morning":
            generate = (query.get("generate") or ["0"])[0] in {"1", "true", "yes"}
            item = runtime.generate_morning_synthesis() if generate else runtime.get_latest_synthesis("morning")
            return (200, item or {"error": "morning_synthesis_not_found"})
        if path == "/api/synthesis/evening":
            generate = (query.get("generate") or ["0"])[0] in {"1", "true", "yes"}
            item = runtime.generate_evening_synthesis() if generate else runtime.get_latest_synthesis("evening")
            return (200, item or {"error": "evening_synthesis_not_found"})
        if path == "/api/interrupts":
            status = (query.get("status") or ["all"])[0]
            limit = int((query.get("limit") or ["50"])[0])
            items = runtime.list_interrupts(status=status, limit=limit)
            return (200, {"count": len(items), "items": items})
        if path == "/api/approvals":
            status = (query.get("status") or ["pending"])[0]
            inbox = ApprovalInbox(runtime.security)
            items = inbox.list(status=status)
            return (200, {"count": len(items), "items": items})
        if path.startswith("/api/approvals/"):
            approval_id = path.split("/")[-1]
            inbox = ApprovalInbox(runtime.security)
            item = inbox.show(approval_id)
            if not item:
                return (404, {"error": "approval_not_found", "approval_id": approval_id})
            return (200, item)
        if path == "/api/codex/tasks":
            status = str((query.get("status") or ["all"])[0]).strip() or None
            limit = int((query.get("limit") or ["30"])[0])
            items = runtime.list_codex_tasks(status=status, limit=limit)
            return (
                200,
                {
                    "count": len(items),
                    "items": items,
                    "summary": runtime.codex_delegation.summarize(limit=max(100, limit)),
                },
            )
        if path.startswith("/api/codex/tasks/"):
            task_id = path.split("/")[-1]
            item = runtime.get_codex_task(task_id=task_id)
            if not item:
                return (404, {"error": "codex_task_not_found", "task_id": task_id})
            return (200, item)
        if path == "/api/academics/overview":
            term_id = (query.get("term_id") or ["current_term"])[0]
            return (200, runtime.get_academics_overview(term_id=term_id) or {"error": "academics_overview_not_found"})
        if path == "/api/academics/risks":
            items = runtime.list_academic_risks()
            return (200, {"count": len(items), "items": items})
        if path == "/api/academics/schedule":
            term_id = (query.get("term_id") or ["current_term"])[0]
            return (200, runtime.get_academics_schedule_context(term_id=term_id) or {"error": "academics_schedule_context_not_found"})
        if path == "/api/academics/windows":
            term_id = (query.get("term_id") or ["current_term"])[0]
            return (200, runtime.get_academics_suppression_windows(term_id=term_id) or {"error": "academics_windows_not_found"})
        if path == "/api/markets/overview":
            account_id = (query.get("account_id") or ["default"])[0]
            limit = int((query.get("limit") or ["20"])[0])
            return (
                200,
                {
                    "risk_posture": runtime.get_market_risk_posture(account_id=account_id),
                    "opportunities": runtime.list_market_opportunities(limit=limit),
                    "abstentions": runtime.list_market_abstentions(limit=limit),
                    "events": runtime.list_market_events(limit=limit),
                    "handoffs": runtime.list_market_handoffs(limit=limit),
                    "outcomes": runtime.list_market_outcomes(limit=limit),
                    "evaluation": runtime.summarize_market_outcomes(limit=max(limit, 60)),
                    "risks": runtime.list_market_risks(),
                },
            )
        if path == "/api/markets/opportunities":
            limit = int((query.get("limit") or ["20"])[0])
            items = runtime.list_market_opportunities(limit=limit)
            return (200, {"count": len(items), "items": items})
        if path == "/api/markets/abstentions":
            limit = int((query.get("limit") or ["20"])[0])
            items = runtime.list_market_abstentions(limit=limit)
            return (200, {"count": len(items), "items": items})
        if path == "/api/markets/posture":
            account_id = (query.get("account_id") or ["default"])[0]
            item = runtime.get_market_risk_posture(account_id=account_id)
            return (200, item or {"error": "market_risk_posture_not_found"})
        if path == "/api/markets/handoffs":
            limit = int((query.get("limit") or ["20"])[0])
            items = runtime.list_market_handoffs(limit=limit)
            return (200, {"count": len(items), "items": items})
        if path == "/api/markets/outcomes":
            limit = int((query.get("limit") or ["20"])[0])
            items = runtime.list_market_outcomes(limit=limit)
            return (200, {"count": len(items), "items": items, "summary": runtime.summarize_market_outcomes(limit=max(limit, 60))})
        if path == "/api/preferences":
            return (200, {
                "preferences": runtime.get_operator_preferences(),
                "events": runtime.list_operator_preference_events(limit=30),
            })
        if path == "/api/preferences/pondering-mode":
            return (200, runtime.get_pondering_mode())
        if path == "/api/identity":
            return (
                200,
                {
                    "user_model": runtime.get_user_model(),
                    "personal_context": runtime.get_personal_context(),
                    "consciousness_contract": runtime.get_consciousness_contract(),
                    "events": runtime.list_identity_events(limit=30),
                },
            )
        if path == "/api/identity/consciousness-contract":
            return (200, runtime.get_consciousness_contract())
        if path == "/api/presence/health":
            return (200, runtime.get_presence_health())
        if path == "/api/presence/nodes":
            status = str((query.get("status") or ["all"])[0]).strip() or None
            limit = int((query.get("limit") or ["50"])[0])
            items = runtime.list_presence_nodes(status=status, limit=limit)
            return (200, {"count": len(items), "items": items})
        if path == "/api/presence/mode":
            decide = _is_truthy((query.get("decide") or ["0"])[0])
            if decide:
                mode = runtime.decide_relationship_mode(
                    explicit_directive=_is_truthy((query.get("explicit_directive") or ["0"])[0]),
                    disputed=_is_truthy((query.get("disputed") or ["0"])[0]),
                    high_stakes=_is_truthy((query.get("high_stakes") or ["0"])[0]),
                    uncertainty=float((query.get("uncertainty") or ["0"])[0]),
                    force_mode=str((query.get("force_mode") or [""])[0]).strip() or None,
                    context={"source": "query"},
                )
            else:
                mode = runtime.get_presence_mode()
            recent_limit = int((query.get("recent_limit") or ["10"])[0])
            return (
                200,
                {
                    "mode": mode,
                    "recent": runtime.relationship_modes.list_recent(limit=recent_limit),
                },
            )
        if path == "/api/presence/constraints":
            return (200, runtime.get_presence_constraints())
        if path == "/api/presence/dialogue/threads":
            status = str((query.get("status") or [""])[0]).strip() or None
            limit = int((query.get("limit") or ["50"])[0])
            items = runtime.list_dialogue_threads(status=status, limit=limit)
            return (200, {"count": len(items), "items": items})
        if path == "/api/presence/dialogue/retrieval":
            return (200, runtime.get_dialogue_retrieval_config())
        if path == "/api/presence/dialogue/snapshot":
            surface_id = str((query.get("surface_id") or [""])[0]).strip()
            session_id = str((query.get("session_id") or [""])[0]).strip()
            if not surface_id or not session_id:
                return (400, {"error": "surface_id_and_session_id_required"})
            turn_limit = int((query.get("turn_limit") or ["20"])[0])
            return (
                200,
                runtime.get_dialogue_thread_snapshot(
                    surface_id=surface_id,
                    session_id=session_id,
                    turn_limit=turn_limit,
                ),
            )
        if path == "/api/presence/sessions":
            status = str((query.get("status") or ["all"])[0]).strip() or None
            limit = int((query.get("limit") or ["50"])[0])
            items = runtime.list_surface_sessions(status=status, limit=limit)
            return (200, {"count": len(items), "items": items})
        if path == "/api/presence/gateway-loop":
            return (200, runtime.get_openclaw_gateway_status())
        if path == "/api/presence/gateway-profile":
            return (200, runtime.get_openclaw_gateway_profile())
        if path == "/api/presence/trust-axes":
            node_id = str((query.get("node_id") or [""])[0]).strip() or None
            command = str((query.get("command") or [""])[0]).strip() or None
            return (200, runtime.get_presence_trust_axes(node_id=node_id, command=command))
        if path == "/api/presence/continuity-snapshot":
            surface_id = str((query.get("surface_id") or [""])[0]).strip() or None
            session_id = str((query.get("session_id") or [""])[0]).strip() or None
            return (
                200,
                runtime.get_presence_continuity_snapshot(
                    surface_id=surface_id,
                    session_id=session_id,
                ),
            )
        if path == "/api/presence/tone-balance":
            limit = int((query.get("limit") or ["30"])[0])
            return (200, runtime.get_presence_tone_balance(limit=limit))
        if path == "/api/presence/adaptive-policy":
            return (
                200,
                {
                    "policy": runtime.get_adaptive_policy(),
                    "revision": runtime.get_adaptive_policy_revision(),
                },
            )
        if path == "/api/presence/adaptive-policy/history":
            limit = int((query.get("limit") or ["30"])[0])
            items = runtime.list_adaptive_policy_history(limit=limit)
            return (200, {"count": len(items), "items": items})
        if path == "/api/presence/self-patch/events":
            limit = int((query.get("limit") or ["30"])[0])
            items = runtime.list_self_patch_events(limit=limit)
            return (200, {"count": len(items), "items": items})
        if path == "/api/presence/voice/soak/report":
            run_id = str((query.get("run_id") or [""])[0]).strip() or None
            limit = int((query.get("limit") or ["500"])[0])
            return (200, runtime.get_voice_continuity_soak_report(run_id=run_id, limit=limit))
        if path == "/api/presence/voice/pack":
            refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
            return (200, runtime.get_active_voice_pack(refresh=refresh))
        if path == "/api/presence/voice/readiness":
            refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
            return (200, runtime.get_voice_readiness_report(refresh=refresh))
        if path == "/api/presence/voice/diagnostics":
            refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
            run_id = str((query.get("run_id") or [""])[0]).strip() or None
            limit = int((query.get("limit") or ["200"])[0])
            return (
                200,
                runtime.get_voice_continuity_diagnostics(
                    run_id=run_id,
                    limit=limit,
                    refresh=refresh,
                ),
            )
        if path == "/api/presence/voice/tuning":
            refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
            run_id = str((query.get("run_id") or [""])[0]).strip() or None
            limit = int((query.get("limit") or ["200"])[0])
            return (
                200,
                runtime.get_voice_tuning_profile(
                    run_id=run_id,
                    limit=limit,
                    refresh=refresh,
                ),
            )
        if path == "/api/presence/voice/tuning/overrides":
            events_limit = int((query.get("events_limit") or ["20"])[0])
            return (
                200,
                {
                    "overrides": runtime.get_voice_tuning_overrides(),
                    "events": runtime.list_voice_tuning_override_events(limit=events_limit),
                },
            )
        if path == "/api/ingest/signals":
            limit = int((query.get("limit") or ["50"])[0])
            items = runtime.list_ingested_signals(limit=limit)
            return (200, {"count": len(items), "items": items})
        if path == "/api/consciousness/surfaces":
            include_content = (query.get("include_content") or ["0"])[0] in {"1", "true", "yes"}
            return (200, runtime.get_consciousness_surfaces(include_content=include_content))
        if path == "/api/consciousness/events":
            limit = int((query.get("limit") or ["100"])[0])
            event_type = str((query.get("event_type") or [""])[0]).strip() or None
            items = runtime.list_consciousness_events(limit=limit, event_type=event_type)
            return (200, {"count": len(items), "items": items})
        if path == "/api/digests":
            limit = int((query.get("limit") or ["30"])[0])
            items = runtime.list_digest_exports(limit=limit)
            return (200, {"count": len(items), "items": items})
        if path.startswith("/api/digests/"):
            day_key = path.split("/")[-1]
            item = runtime.get_digest_export(day_key)
            if not item:
                return (404, {"error": "digest_not_found", "day_key": day_key})
            return (200, item)
        return (404, {"error": "not_found", "path": path})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/index.html"}:
            self._send_html(HTTPStatus.OK, _DASHBOARD_HTML)
            return
        query = parse_qs(parsed.query)
        with self.server.runtime_lock:
            status, payload = self._route_get(path, query)
        self._send_json(status, payload)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()
        runtime = self.server.runtime

        with self.server.runtime_lock:
            if path in {"/api/ingest", "/api/ingest/signal"}:
                provided_token = str(
                    self.headers.get("X-JARVIS-Token")
                    or self.headers.get("X-Jarvis-Token")
                    or self.headers.get("Authorization")
                    or ""
                ).strip()
                if provided_token.lower().startswith("bearer "):
                    provided_token = provided_token[7:].strip()
                if not runtime.ingest_token_valid(provided_token):
                    self._send_json(401, {"error": "unauthorized"})
                    return
                signal = body.get("signal") if isinstance(body.get("signal"), dict) else body.get("envelope")
                if not isinstance(signal, dict):
                    signal = body if isinstance(body, dict) else {}
                try:
                    payload = runtime.ingest_signal(signal, auth_context="operator_ingest_api")
                except ValueError as exc:
                    self._send_json(400, {"error": "invalid_signal_envelope", "detail": str(exc)})
                    return
                status = 202 if payload.get("duplicate") else 200
                self._send_json(status, payload)
                return
            if path.startswith("/api/interrupts/") and path.endswith("/acknowledge"):
                interrupt_id = path.split("/")[-2]
                actor = str(body.get("actor") or "operator")
                payload = runtime.acknowledge_interrupt(interrupt_id, actor=actor)
                self._send_json(200, payload)
                return
            if path.startswith("/api/interrupts/") and path.endswith("/snooze"):
                interrupt_id = path.split("/")[-2]
                actor = str(body.get("actor") or "operator")
                minutes = int(body.get("minutes") or 60)
                payload = runtime.snooze_interrupt(interrupt_id, minutes=minutes, actor=actor)
                self._send_json(200, payload)
                return
            if path == "/api/preferences/focus-mode":
                domain = body.get("domain")
                actor = str(body.get("actor") or "operator")
                payload = runtime.set_focus_mode(domain=str(domain) if domain is not None else None, actor=actor)
                self._send_json(200, payload)
                return
            if path == "/api/preferences/quiet-hours":
                actor = str(body.get("actor") or "operator")
                start_hour = body.get("start_hour")
                end_hour = body.get("end_hour")
                payload = runtime.set_quiet_hours(
                    start_hour=int(start_hour) if start_hour is not None else None,
                    end_hour=int(end_hour) if end_hour is not None else None,
                    actor=actor,
                )
                self._send_json(200, payload)
                return
            if path == "/api/preferences/suppress-until":
                actor = str(body.get("actor") or "operator")
                payload = runtime.suppress_interrupts_until(
                    until_iso=str(body.get("until_iso") or "").strip() or None,
                    reason=str(body.get("reason") or ""),
                    actor=actor,
                )
                self._send_json(200, payload)
                return
            if path == "/api/preferences/pondering-mode":
                actor = str(body.get("actor") or "operator")
                enabled_raw = body.get("enabled")
                enabled = bool(enabled_raw) if enabled_raw is not None else None
                style = (
                    str(body.get("style") or "").strip()
                    if body.get("style") is not None
                    else None
                )
                min_confidence = body.get("min_confidence_for_understood")
                payload = runtime.set_pondering_mode(
                    enabled=enabled,
                    style=style,
                    min_confidence_for_understood=(
                        float(min_confidence)
                        if min_confidence is not None
                        else None
                    ),
                    actor=actor,
                )
                self._send_json(200, payload)
                return
            if path == "/api/identity/domain-weight":
                actor = str(body.get("actor") or "operator")
                payload = runtime.set_domain_weight(
                    domain=str(body.get("domain") or ""),
                    weight=float(body.get("weight") or 1.0),
                    actor=actor,
                )
                self._send_json(200, payload)
                return
            if path == "/api/identity/context":
                actor = str(body.get("actor") or "operator")
                available_focus_minutes = (
                    int(body["available_focus_minutes"])
                    if body.get("available_focus_minutes") is not None
                    else (
                        int(body["focus_minutes"])
                        if body.get("focus_minutes") is not None
                        else None
                    )
                )
                payload = runtime.update_personal_context(
                    stress_level=float(body["stress_level"]) if body.get("stress_level") is not None else None,
                    energy_level=float(body["energy_level"]) if body.get("energy_level") is not None else None,
                    sleep_hours=float(body["sleep_hours"]) if body.get("sleep_hours") is not None else None,
                    available_focus_minutes=available_focus_minutes,
                    mode=str(body.get("mode") or "").strip() or None,
                    note=str(body.get("note") or "").strip() or None,
                    actor=actor,
                )
                self._send_json(200, payload)
                return
            if path == "/api/identity/consciousness-contract":
                actor = str(body.get("actor") or "operator")
                replace = bool(body.get("replace"))
                patch = body.get("patch") if isinstance(body.get("patch"), dict) else body
                payload = runtime.update_consciousness_contract(
                    patch=dict(patch or {}),
                    actor=actor,
                    replace=replace,
                )
                runtime.refresh_consciousness_surfaces(reason="identity_contract_update")
                self._send_json(200, payload)
                return
            if path == "/api/presence/nodes/pair":
                actor = str(body.get("actor") or "operator")
                metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
                try:
                    payload = runtime.pair_presence_node(
                        node_id=str(body.get("node_id") or ""),
                        device_id=str(body.get("device_id") or ""),
                        owner_id=str(body.get("owner_id") or ""),
                        gateway_token_ref=str(body.get("gateway_token_ref") or ""),
                        node_token_ref=str(body.get("node_token_ref") or ""),
                        pairing_status=str(body.get("pairing_status") or "paired"),
                        metadata=metadata,
                        actor=actor,
                    )
                except ValueError as exc:
                    self._send_json(400, {"error": "invalid_presence_pairing", "detail": str(exc)})
                    return
                self._send_json(200, payload)
                return
            if path.startswith("/api/presence/nodes/") and path.endswith("/revoke"):
                node_id = path.split("/")[-2]
                actor = str(body.get("actor") or "operator")
                reason = str(body.get("reason") or "")
                payload = runtime.revoke_presence_node(node_id=node_id, reason=reason, actor=actor)
                if not payload:
                    self._send_json(404, {"error": "presence_node_not_found", "node_id": node_id})
                    return
                self._send_json(200, payload)
                return
            if path == "/api/presence/mode":
                force_mode = str(body.get("force_mode") or "").strip() or None
                context = body.get("context") if isinstance(body.get("context"), dict) else {}
                payload = runtime.decide_relationship_mode(
                    explicit_directive=_is_truthy(body.get("explicit_directive")),
                    disputed=_is_truthy(body.get("disputed")),
                    high_stakes=_is_truthy(body.get("high_stakes")),
                    uncertainty=float(body.get("uncertainty") or 0.0),
                    force_mode=force_mode,
                    context=context,
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/openclaw-event":
                event = body.get("event") if isinstance(body.get("event"), dict) else body
                payload = runtime.ingest_openclaw_gateway_event(event if isinstance(event, dict) else {})
                self._send_json(200, payload)
                return
            if path == "/api/presence/heartbeat":
                payload = runtime.run_presence_heartbeat()
                self._send_json(200, payload)
                return
            if path == "/api/presence/node-command/broker":
                payload = runtime.broker_node_command(
                    command=str(body.get("command") or ""),
                    payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
                    actor=str(body.get("actor") or "operator"),
                )
                self._send_json(200, payload)
                return
            if path == "/api/codex/tasks":
                text = str(body.get("text") or body.get("prompt") or "").strip()
                if not text:
                    self._send_json(400, {"error": "codex_text_required"})
                    return
                context = body.get("context") if isinstance(body.get("context"), dict) else {}
                for top_key in ("effort_tier", "tier", "reasoning_tier", "reasoning_effort", "codex_model"):
                    if body.get(top_key) is not None and top_key not in context:
                        context[top_key] = body.get(top_key)
                payload = runtime.create_codex_task(
                    text=text,
                    source_surface=str(body.get("source_surface") or body.get("surface_id") or ""),
                    session_id=str(body.get("session_id") or ""),
                    actor=str(body.get("actor") or "owner"),
                    write_enabled=_is_truthy(body.get("write_enabled")) if body.get("write_enabled") is not None else True,
                    auto_execute=(
                        _is_truthy(body.get("auto_execute"))
                        if body.get("auto_execute") is not None
                        else None
                    ),
                    context=context,
                )
                self._send_json(200, payload)
                return
            if path.startswith("/api/codex/tasks/") and path.endswith("/execute"):
                task_id = path.split("/")[-2]
                payload = runtime.execute_codex_task(
                    task_id=task_id,
                    background=(
                        _is_truthy(body.get("background"))
                        if body.get("background") is not None
                        else True
                    ),
                )
                status_code = 200 if bool(payload.get("ok")) else 404
                self._send_json(status_code, payload)
                return
            if path == "/api/presence/reply/prepare":
                draft = body.get("draft") if isinstance(body.get("draft"), dict) else body
                payload = runtime.prepare_openclaw_reply(draft if isinstance(draft, dict) else {})
                self._send_json(200, payload)
                return
            if path == "/api/presence/router/preview":
                text = str(body.get("text") or body.get("prompt") or "").strip()
                context = body.get("context") if isinstance(body.get("context"), dict) else {}
                for top_key in (
                    "effort_tier",
                    "tier",
                    "reasoning_tier",
                    "reasoning_effort",
                    "execution_engine",
                    "engine",
                    "route_engine",
                    "codex_delegate",
                    "codex_auto_execute",
                    "codex_model",
                ):
                    if body.get(top_key) is not None and top_key not in context:
                        context[top_key] = body.get(top_key)
                payload = runtime.preview_work_item_route(
                    text=text,
                    context=context,
                    explicit_directive=_is_truthy(body.get("explicit_directive")),
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/voice/reply/prepare":
                draft = body.get("draft") if isinstance(body.get("draft"), dict) else body
                payload = runtime.prepare_openclaw_voice_reply(draft if isinstance(draft, dict) else {})
                self._send_json(200, payload)
                return
            if path == "/api/presence/voice/soak/start":
                payload = runtime.start_voice_continuity_soak(
                    run_id=str(body.get("run_id") or "").strip() or None,
                    label=str(body.get("label") or "").strip() or None,
                    metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/voice/soak/turn":
                draft = body.get("draft") if isinstance(body.get("draft"), dict) else {}
                observed = (
                    body.get("observed_latencies_ms")
                    if isinstance(body.get("observed_latencies_ms"), dict)
                    else {}
                )
                payload = runtime.record_voice_continuity_soak_turn(
                    run_id=str(body.get("run_id") or "").strip(),
                    draft=draft,
                    observed_latencies_ms=observed,
                    interrupted=_is_truthy(body.get("interrupted")),
                    interruption_recovered=_is_truthy(body.get("interruption_recovered")),
                    expected_mode=str(body.get("expected_mode") or "").strip() or None,
                    pushback_outcome=str(body.get("pushback_outcome") or "none"),
                    mismatch_suppressed=_is_truthy(body.get("mismatch_suppressed")),
                    note=str(body.get("note") or "").strip() or None,
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/voice/tuning/overrides":
                patch = body.get("patch") if isinstance(body.get("patch"), dict) else body
                if not isinstance(patch, dict):
                    self._send_json(400, {"error": "voice_tuning_patch_required"})
                    return
                payload = runtime.update_voice_tuning_overrides(
                    patch=patch,
                    replace=_is_truthy(body.get("replace")),
                    actor=str(body.get("actor") or "operator"),
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/voice/tuning/overrides/reset":
                payload = runtime.reset_voice_tuning_overrides(
                    actor=str(body.get("actor") or "operator"),
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/taskflow-cycle":
                reason = str(body.get("reason") or "taskflow_presence_cycle")
                payload = runtime.run_taskflow_presence_cycle(reason=reason)
                self._send_json(200, payload)
                return
            if path == "/api/presence/adaptive-policy/update":
                patch = body.get("patch") if isinstance(body.get("patch"), dict) else {}
                if not patch:
                    self._send_json(400, {"error": "adaptive_patch_required"})
                    return
                payload = runtime.update_adaptive_policy(
                    patch=patch,
                    reason=str(body.get("reason") or "operator_update"),
                    metrics=body.get("metrics") if isinstance(body.get("metrics"), dict) else {},
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/adaptive-policy/calibrate":
                payload = runtime.run_adaptive_calibration(
                    reason=str(body.get("reason") or "operator_calibration"),
                    apply=(
                        _is_truthy(body.get("apply"))
                        if body.get("apply") is not None
                        else True
                    ),
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/self-patch/quota":
                if body.get("weekly_remaining_percent") is None:
                    self._send_json(400, {"error": "weekly_remaining_percent_required"})
                    return
                try:
                    weekly_remaining_percent = float(body["weekly_remaining_percent"])
                except (TypeError, ValueError):
                    self._send_json(400, {"error": "weekly_remaining_percent_invalid"})
                    return
                min_weekly_remaining_percent = None
                if body.get("min_weekly_remaining_percent") is not None:
                    try:
                        min_weekly_remaining_percent = float(body["min_weekly_remaining_percent"])
                    except (TypeError, ValueError):
                        self._send_json(400, {"error": "min_weekly_remaining_percent_invalid"})
                        return
                payload = runtime.update_self_patch_quota(
                    weekly_remaining_percent=weekly_remaining_percent,
                    min_weekly_remaining_percent=min_weekly_remaining_percent,
                    actor=str(body.get("actor") or "operator"),
                    reason=str(body.get("reason") or "operator_quota_update"),
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/self-patch/trigger":
                issue = str(body.get("issue") or body.get("text") or "").strip()
                if not issue:
                    self._send_json(400, {"error": "self_patch_issue_required"})
                    return
                weekly_remaining_percent = None
                if body.get("weekly_remaining_percent") is not None:
                    try:
                        weekly_remaining_percent = float(body["weekly_remaining_percent"])
                    except (TypeError, ValueError):
                        self._send_json(400, {"error": "weekly_remaining_percent_invalid"})
                        return
                payload = runtime.trigger_self_patch_task(
                    issue=issue,
                    reason=str(body.get("reason") or "operator_trigger"),
                    effort_tier=str(body.get("effort_tier") or "pro"),
                    auto_execute=(
                        _is_truthy(body.get("auto_execute"))
                        if body.get("auto_execute") is not None
                        else None
                    ),
                    metrics=body.get("metrics") if isinstance(body.get("metrics"), dict) else {},
                    project_scope=str(body.get("project_scope") or "").strip() or None,
                    approval_source=str(body.get("approval_source") or "").strip() or None,
                    change_impact=str(body.get("change_impact") or "").strip() or None,
                    requested_capabilities=(
                        body.get("requested_capabilities")
                        if isinstance(body.get("requested_capabilities"), list)
                        else []
                    ),
                    external_access=(
                        _is_truthy(body.get("external_access"))
                        if body.get("external_access") is not None
                        else None
                    ),
                    weekly_remaining_percent=weekly_remaining_percent,
                )
                self._send_json(200 if bool(payload.get("ok")) else 400, payload)
                return
            if path == "/api/presence/continuity-freeze-check":
                payload = runtime.check_presence_continuity_freeze(
                    primary_surface_id=str(body.get("primary_surface_id") or ""),
                    primary_session_id=str(body.get("primary_session_id") or ""),
                    secondary_surface_id=str(body.get("secondary_surface_id") or ""),
                    secondary_session_id=str(body.get("secondary_session_id") or ""),
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/gateway-loop/configure":
                payload = runtime.configure_openclaw_gateway_loop(
                    ws_url=str(body.get("ws_url") or "").strip() or None,
                    token_ref=str(body.get("token_ref") or "").strip() or None,
                    owner_id=str(body.get("owner_id") or "").strip() or None,
                    client_name=str(body.get("client_name") or "").strip() or None,
                    protocol_profile_id=str(body.get("protocol_profile_id") or "").strip() or None,
                    protocol_profile_path=str(body.get("protocol_profile_path") or "").strip() or None,
                    allow_remote=_is_truthy(body.get("allow_remote")) if body.get("allow_remote") is not None else None,
                    enabled=_is_truthy(body.get("enabled")) if body.get("enabled") is not None else None,
                    connect_timeout_seconds=(
                        float(body["connect_timeout_seconds"])
                        if body.get("connect_timeout_seconds") is not None
                        else None
                    ),
                    heartbeat_interval_seconds=(
                        float(body["heartbeat_interval_seconds"])
                        if body.get("heartbeat_interval_seconds") is not None
                        else None
                    ),
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/gateway-loop/start":
                payload = runtime.start_openclaw_gateway_loop()
                self._send_json(200, payload)
                return
            if path == "/api/presence/gateway-loop/stop":
                payload = runtime.stop_openclaw_gateway_loop()
                self._send_json(200, payload)
                return
            if path == "/api/presence/gateway-loop/pump":
                max_messages = int(body.get("max_messages") or 120)
                payload = runtime.pump_openclaw_gateway(max_messages=max_messages)
                self._send_json(200, payload)
                return
            if path == "/api/presence/gateway-loop/soak":
                payload = runtime.run_openclaw_gateway_soak(
                    loops=int(body.get("loops") or 12),
                    max_messages=int(body.get("max_messages") or 120),
                    node_id=str(body.get("node_id") or "").strip() or None,
                    probe_command=str(body.get("probe_command") or "notifications.send"),
                    expect_pairing_approved=_is_truthy(body.get("expect_pairing_approved")),
                )
                self._send_json(200, payload)
                return
            if path == "/api/presence/gateway-loop/node-soak":
                payload = runtime.run_openclaw_node_embodiment_soak(
                    ws_url=str(body.get("ws_url") or "").strip() or None,
                    token_ref=str(body.get("token_ref") or "").strip() or None,
                    owner_id=str(body.get("owner_id") or "primary_operator"),
                    client_name=str(body.get("client_name") or "jarvis"),
                    node_display_name=str(body.get("node_display_name") or "JARVIS-Soak-Node"),
                    profile_prefix=str(body.get("profile_prefix") or "jarvis-m18-node-soak"),
                    probe_command=str(body.get("probe_command") or "notifications.send"),
                    pairing_timeout_seconds=float(body.get("pairing_timeout_seconds") or 45.0),
                    reconnect_timeout_seconds=float(body.get("reconnect_timeout_seconds") or 35.0),
                    run_reject_cycle=(
                        _is_truthy(body.get("run_reject_cycle"))
                        if body.get("run_reject_cycle") is not None
                        else True
                    ),
                )
                self._send_json(200, payload)
                return
            if path == "/api/consciousness/refresh":
                reason = str(body.get("reason") or "operator_refresh")
                payload = runtime.refresh_consciousness_surfaces(reason=reason)
                self._send_json(200, payload)
                return
            if path == "/api/digests/export":
                day_key = str(body.get("day_key") or "").strip() or None
                payload = runtime.export_daily_digest(day_key=day_key)
                self._send_json(200, payload)
                return

        self._send_json(404, {"error": "not_found", "path": path})


def run_operator_server(
    *,
    repo_path: str | Path,
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    runtime = JarvisRuntime(db_path=Path(db_path).resolve(), repo_path=Path(repo_path).resolve())
    server = OperatorHttpServer((host, int(port)), runtime)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        runtime.close()
