#!/usr/bin/env python3
"""Eyewitness end-to-end check, run on staging inside the app env.

Claude Code runs this on staging after deploying a Phase 2 build:

    venv/bin/python eyewitness/tools/e2e_staging.py \
        --tenant <tenant uuid> \
        --golden /path/to/windows11-crash-screen-0x1A.png \
        --base-url <staging base URL>

It creates a ticket as Brenda in the staging Zammad, polls it in,
checks that Eyewitness took it, plays the customer over the public
token routes, and checks the reading, the internal summary note, the
release and the cost rows. One PASS or FAIL line per step. It prints
no secrets and no evidence tokens. The Zammad ticket is closed at the
end; the Wegweiser rows stay as the demo record.
"""
from __future__ import annotations

import argparse
import sys
import time

ZAMMAD_ORG_ID = 2
BRENDA_ID = 5
GROUP_NAME = "Users"

FAILURES = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    print(("PASS " if ok else "FAIL ") + name
          + (f" - {detail}" if detail else ""), flush=True)
    if not ok:
        FAILURES.append(name)
    return ok


def wait_for(fn, timeout: int, interval: float = 3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return fn()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", required=True,
                        help="path to the golden crash-screen image")
    parser.add_argument("--base-url", required=True,
                        help="the app base URL, no trailing slash")
    parser.add_argument("--tenant", required=True,
                        help="the demo tenant uuid")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    import requests
    from app import create_app
    app = create_app()

    with app.app_context():
        from app.models import db
        from app.eyewitness.models import (
            EvidenceRequest, IntakeRun, TenantSettings)
        import uuid as uuid_mod
        tenant_uuid = uuid_mod.UUID(args.tenant)

        # Eyewitness on for the demo tenant (its Phase 2 test bed).
        settings = db.session.get(TenantSettings, tenant_uuid)
        if settings is None:
            settings = TenantSettings(tenant_uuid=tenant_uuid,
                                      enabled=True,
                                      auto_send_templates=True)
            db.session.add(settings)
        else:
            settings.enabled = True
        db.session.commit()

        from app.eyewitness.connectors.zammad import ZammadConnector
        connector, reason = ZammadConnector.for_tenant(tenant_uuid)
        if not step("connector for tenant", connector is not None,
                    str(reason or "")):
            return 1
        client = connector._client

        title = "Computer crashes to a black screen and now blinks"
        body = ("<p>My computer keeps crashing. The screen goes black "
                "and says it ran into a problem and needs to restart, "
                "with a small stop code line at the bottom. It did this "
                "twice this morning. Now it will not start at all and "
                "the power light keeps blinking.</p>")
        ticket = client.create_ticket(
            group=GROUP_NAME, title=title, body_html=body,
            customer_id=BRENDA_ID, organization_id=ZAMMAD_ORG_ID)
        ext_id = str(ticket.get("id"))
        if not step("ticket created as Brenda", bool(ext_id),
                    f"zammad id {ext_id}"):
            return 1

        try:
            from app.utilities.helpdesk_inbound import poll_tenant
            summary = poll_tenant(tenant_uuid)
            step("inbound poll", bool(summary.get("ok")),
                 f"seen={summary.get('seen')} new={summary.get('new')}")

            from app.models.helpdesk import HelpdeskTicket
            rec = HelpdeskTicket.query.filter_by(
                tenant_uuid=tenant_uuid, provider="zammad",
                external_id=ext_id).first()
            if not step("ticket indexed", rec is not None):
                return 1

            def find_run():
                db.session.expire_all()
                return IntakeRun.query.filter_by(
                    helpdesk_ticket_id=rec.id).order_by(
                        IntakeRun.id.desc()).first()

            run = wait_for(
                lambda: (lambda r: r if r and r.status
                         == "evidence_pending" else None)(find_run()),
                args.timeout)
            if not step("intake run holds the ticket",
                        run is not None and run.status == "evidence_pending",
                        f"status={getattr(run, 'status', None)}"):
                return 1

            def find_request():
                db.session.expire_all()
                return EvidenceRequest.query.filter_by(
                    intake_run_id=run.id).order_by(
                        EvidenceRequest.id.desc()).first()

            req = wait_for(find_request, 30)
            templates = [i.get("template") for i in (req.items or [])] \
                if req else []
            step("evidence request with template items",
                 bool(req and templates), f"items={templates}")
            step("request open for the customer",
                 bool(req and req.status == "open"),
                 f"status={getattr(req, 'status', None)}")
            arts = client.ticket_articles(ext_id)
            public = [a for a in arts if not a.get("internal")
                      and "/eyewitness/" in str(a.get("body") or "")]
            step("customer-visible article posted", bool(public),
                 f"articles={len(arts)}")
            if not req:
                return 1

            # Play the customer over the public token routes.
            token = req.token
            with open(args.golden, "rb") as fh:
                golden_bytes = fh.read()
            screen_item = next(
                (i for i in req.items if i.get("template")
                 in ("screen_photo", "screen_photo_next_time")), None)
            up = requests.post(
                f"{base}/eyewitness/{token}/upload",
                data={"item_id": screen_item["id"]},
                files={"file": ("crash.png", golden_bytes, "image/png")},
                timeout=60)
            cap_id = (up.json() or {}).get("capture_id") \
                if up.status_code == 201 else None
            step("golden photo uploaded", cap_id is not None,
                 f"http={up.status_code}")

            def capture_state():
                if not cap_id:
                    return None
                got = requests.get(
                    f"{base}/eyewitness/{token}/capture/{cap_id}",
                    timeout=30)
                return got.json() if got.status_code == 200 else None

            state = wait_for(
                lambda: (lambda s: s if s and s.get("status")
                         in ("ok", "retake", "failed") else None)(
                             capture_state()),
                args.timeout)
            step("screen verdict returned",
                 bool(state and state.get("status") == "ok"),
                 f"status={(state or {}).get('status')}")

            for item in (req.items or []):
                if item.get("kind") == "question":
                    opts = item.get("options") or []
                    requests.post(
                        f"{base}/eyewitness/{token}/answer",
                        json={"item_id": item["id"],
                              "answer": opts[1] if len(opts) > 1
                              else "this week"}, timeout=30)
            video_item = next(
                (i for i in req.items
                 if i.get("template") == "power_light_video"), None)
            if video_item:
                requests.post(f"{base}/eyewitness/{token}/skip",
                              json={"item_id": video_item["id"],
                                    "reason": "can't do this"},
                              timeout=30)
            step("video item skipped", True,
                 "skipped" if video_item else "no video item was asked")
            requests.post(f"{base}/eyewitness/{token}/voice-offer",
                          json={"accept": False}, timeout=30)
            step("voice offer declined", True)

            from app.eyewitness.models import Capture
            cap = db.session.get(Capture, cap_id) if cap_id else None
            reading = (cap.reading or {}) if cap else {}
            step("reading holds the stop code",
                 "MEMORY_MANAGEMENT" in str(reading.get("stop_code") or ""),
                 f"stop_code={reading.get('stop_code')}")

            def summary_posted():
                db.session.expire_all()
                found = client.ticket_articles(ext_id)
                return [a for a in found if a.get("internal")
                        and "MEMORY_MANAGEMENT" in str(a.get("body") or "")
                        and "Eyewitness" in str(a.get("body") or "")]

            notes = wait_for(summary_posted, args.timeout)
            step("internal summary note in Zammad", bool(notes),
                 f"matches={len(notes or [])}")

            def released_run():
                db.session.expire_all()
                row = db.session.get(IntakeRun, run.id)
                return row if row and row.status == "released" else None

            done_run = wait_for(released_run, args.timeout)
            step("ticket released to the governor", done_run is not None,
                 f"released_at={getattr(done_run, 'released_at', None)}")

            from app.eyewitness.intake import release_run
            step("second release is refused (queued exactly once)",
                 release_run(run.id) is False)

            def governor_moved():
                db.session.expire_all()
                row = db.session.get(HelpdeskTicket, rec.id)
                return row if row and row.triage_status != "pending" \
                    else None

            gov = wait_for(governor_moved, args.timeout)
            step("governor took the enriched ticket", gov is not None,
                 f"triage={getattr(gov, 'triage_status', None)}")

            from app.eyewitness.models import MetaCall
            cap_ids = [c.id for c in Capture.query.filter_by(
                request_id=req.id).all()]
            calls = MetaCall.query.filter(
                (MetaCall.intake_run_id == run.id)
                | (MetaCall.capture_id.in_(cap_ids or [-1]))).all()
            total = sum(float(c.usd or 0) for c in calls)
            step("cost rows logged", bool(calls) and total > 0,
                 f"calls={len(calls)} usd={total:.4f}")
        finally:
            try:
                client.close_ticket(ext_id)
                print("NOTE ticket closed in the staging Zammad; "
                      "Wegweiser rows stay as the demo record")
            except Exception as exc:
                print(f"NOTE ticket close skipped: {str(exc)[:120]}")

    print("E2E: " + ("FAIL" if FAILURES else "PASS")
          + (f" ({', '.join(FAILURES)})" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
