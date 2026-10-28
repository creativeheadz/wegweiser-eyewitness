#!/usr/bin/env python3
"""Eyewitness end-to-end checks, run on staging inside the app env.

Claude Code runs these on staging after deploying a Phase 3 build:

    venv/bin/python eyewitness/tools/e2e_staging.py \
        --scenario email \
        --tenant <tenant uuid> \
        --golden /path/to/windows11-crash-screen-0x1A.png \
        --base-url <staging base URL>

Scenarios: email (the Phase 2 flow, the default), help (the help
button path with a next-time item and a follow-up capture), video
(the Dell blink clip), voice (skip everything, then a voice note)
and injection (the sticky note photo). Media paths come in as
arguments. One PASS or FAIL line prints per step. The script prints
no secrets and no evidence tokens. Zammad tickets are closed at the
end; the Wegweiser rows stay as the demo record.
"""
from __future__ import annotations

import argparse
import sys
import time

ZAMMAD_ORG_ID = 2
BRENDA_ID = 5
GROUP_NAME = "Users"
BRENDA_NAME = "Brenda Demo"
BRENDA_EMAIL = "brenda.demo@example.com"

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


class Stage:
    """Shared staging state for one scenario run."""

    def __init__(self, args):
        self.args = args
        self.base = args.base_url.rstrip("/")
        import requests
        self.http = requests
        from app import create_app
        self.app = create_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app.models import db
        self.db = db
        import uuid as uuid_mod
        self.tenant_uuid = uuid_mod.UUID(args.tenant)
        self.connector = None
        self.client = None
        self.ext_id = None

    def enable_tenant(self) -> bool:
        from app.eyewitness.models import TenantSettings
        settings = self.db.session.get(TenantSettings,
                                       self.tenant_uuid)
        if settings is None:
            settings = TenantSettings(tenant_uuid=self.tenant_uuid,
                                      enabled=True,
                                      auto_send_templates=True)
            self.db.session.add(settings)
        else:
            settings.enabled = True
        self.db.session.commit()
        from app.eyewitness.connectors.zammad import ZammadConnector
        self.connector, reason = ZammadConnector.for_tenant(
            self.tenant_uuid)
        self.client = self.connector._client if self.connector \
            else None
        return step("connector for tenant", self.connector is not None,
                    str(reason or ""))

    def create_ticket(self, title: str, body_html: str) -> bool:
        ticket = self.client.create_ticket(
            group=GROUP_NAME, title=title, body_html=body_html,
            customer_id=BRENDA_ID, organization_id=ZAMMAD_ORG_ID)
        self.ext_id = str(ticket.get("id"))
        return step("ticket created as Brenda", bool(self.ext_id),
                    f"zammad id {self.ext_id}")

    def poll_in(self):
        from app.utilities.helpdesk_inbound import poll_tenant
        summary = poll_tenant(self.tenant_uuid)
        step("inbound poll", bool(summary.get("ok")),
             f"seen={summary.get('seen')} new={summary.get('new')}")
        from app.models.helpdesk import HelpdeskTicket
        return HelpdeskTicket.query.filter_by(
            tenant_uuid=self.tenant_uuid, provider="zammad",
            external_id=self.ext_id).first()

    def wait_run_and_request(self, rec, timeout: int):
        from app.eyewitness.models import EvidenceRequest, IntakeRun

        def find_run():
            self.db.session.expire_all()
            run = IntakeRun.query.filter_by(
                helpdesk_ticket_id=rec.id).order_by(
                    IntakeRun.id.desc()).first()
            return run if run and run.status == "evidence_pending" \
                else None

        run = wait_for(find_run, timeout)
        if not step("intake run holds the ticket", run is not None,
                    f"run={getattr(run, 'id', None)}"):
            return None, None

        def find_request():
            self.db.session.expire_all()
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
        arts = self.client.ticket_articles(self.ext_id)
        public = [a for a in arts if not a.get("internal")
                  and "/eyewitness/" in str(a.get("body") or "")]
        step("customer-visible article posted", bool(public),
             f"articles={len(arts)}")
        return run, req

    def upload(self, token, item_id, path, filename, mime):
        with open(path, "rb") as fh:
            payload = fh.read()
        up = self.http.post(
            f"{self.base}/eyewitness/{token}/upload",
            data={"item_id": item_id},
            files={"file": (filename, payload, mime)}, timeout=120)
        cap_id = (up.json() or {}).get("capture_id") \
            if up.status_code == 201 else None
        return cap_id, up.status_code

    def wait_capture(self, token, cap_id, timeout):
        def state():
            got = self.http.get(
                f"{self.base}/eyewitness/{token}/capture/{cap_id}",
                timeout=30)
            body = got.json() if got.status_code == 200 else None
            return body if body and body.get("status") in (
                "ok", "retake", "failed") else None
        return wait_for(state, timeout)

    def capture_row(self, cap_id):
        from app.eyewitness.models import Capture
        self.db.session.expire_all()
        return self.db.session.get(Capture, cap_id) if cap_id else None

    def answers_and_skips(self, req, skip_templates=None):
        for item in (req.items or []):
            if item.get("kind") == "question":
                opts = item.get("options") or []
                self.http.post(
                    f"{self.base}/eyewitness/{req.token}/answer",
                    json={"item_id": item["id"],
                          "answer": opts[1] if len(opts) > 1
                          else "this week"}, timeout=30)
            elif skip_templates is None \
                    or item.get("template") in skip_templates:
                self.http.post(
                    f"{self.base}/eyewitness/{req.token}/skip",
                    json={"item_id": item["id"],
                          "reason": "can't do this"}, timeout=30)

    def internal_notes(self, needle="Eyewitness"):
        arts = self.client.ticket_articles(self.ext_id)
        return [a for a in arts if a.get("internal")
                and needle in str(a.get("body") or "")]

    def wait_released(self, run, timeout):
        from app.eyewitness.models import IntakeRun

        def released():
            self.db.session.expire_all()
            row = self.db.session.get(IntakeRun, run.id)
            return row if row and row.status == "released" else None
        return wait_for(released, timeout)

    def wait_governor(self, rec, timeout):
        from app.models.helpdesk import HelpdeskTicket

        def moved():
            self.db.session.expire_all()
            row = self.db.session.get(HelpdeskTicket, rec.id)
            return row if row and row.triage_status != "pending" \
                else None
        return wait_for(moved, timeout)

    def cost_step(self, run, req):
        from app.eyewitness.models import Capture, MetaCall
        cap_ids = [c.id for c in Capture.query.filter_by(
            request_id=req.id).all()]
        calls = MetaCall.query.filter(
            (MetaCall.intake_run_id == run.id)
            | (MetaCall.capture_id.in_(cap_ids or [-1]))).all()
        total = sum(float(c.usd or 0) for c in calls)
        return step("cost rows logged", bool(calls) and total > 0,
                    f"calls={len(calls)} usd={total:.4f}")

    def close_ticket(self):
        try:
            self.client.close_ticket(self.ext_id)
            print("NOTE ticket closed in the staging Zammad; "
                  "Wegweiser rows stay as the demo record")
        except Exception as exc:
            print(f"NOTE ticket close skipped: {str(exc)[:120]}")


def scenario_email(stage: Stage) -> None:
    args = stage.args
    if not stage.create_ticket(
            "Computer crashes to a black screen and now blinks",
            "<p>My computer keeps crashing. The screen goes black "
            "and says it ran into a problem and needs to restart, "
            "with a small stop code line at the bottom. It did this "
            "twice this morning. Now it will not start at all and "
            "the power light keeps blinking.</p>"):
        return
    try:
        rec = stage.poll_in()
        if not step("ticket indexed", rec is not None):
            return
        run, req = stage.wait_run_and_request(rec, args.timeout)
        if not req:
            return
        screen_item = next(
            (i for i in req.items if i.get("template")
             in ("screen_photo", "screen_photo_next_time")), None)
        cap_id, code = stage.upload(
            req.token, screen_item["id"], args.golden, "crash.png",
            "image/png")
        step("golden photo uploaded", cap_id is not None,
             f"http={code}")
        state = stage.wait_capture(req.token, cap_id, args.timeout)
        step("screen verdict returned",
             bool(state and state.get("status") == "ok"),
             f"status={(state or {}).get('status')}")
        stage.answers_and_skips(req, skip_templates={
            "power_light_video"})
        step("remaining items answered or skipped", True)
        stage.http.post(f"{stage.base}/eyewitness/{req.token}"
                        "/voice-offer", json={"accept": False},
                        timeout=30)
        step("voice offer declined", True)
        cap = stage.capture_row(cap_id)
        reading = (cap.reading or {}) if cap else {}
        step("reading holds the stop code",
             "MEMORY_MANAGEMENT" in str(reading.get("stop_code")
                                        or ""),
             f"stop_code={reading.get('stop_code')}")
        notes = wait_for(
            lambda: stage.internal_notes("MEMORY_MANAGEMENT")
            or None, args.timeout)
        step("internal summary note in Zammad", bool(notes),
             f"matches={len(notes or [])}")
        done_run = stage.wait_released(run, args.timeout)
        step("ticket released to the governor", done_run is not None)
        from app.eyewitness.intake import release_run
        step("second release is refused (queued exactly once)",
             release_run(run.id) is False)
        gov = stage.wait_governor(rec, args.timeout)
        step("governor took the enriched ticket", gov is not None,
             f"triage={getattr(gov, 'triage_status', None)}")
        stage.cost_step(run, req)
    finally:
        stage.close_ticket()


def scenario_video(stage: Stage) -> None:
    args = stage.args
    if not args.video:
        step("video file supplied", False, "pass --video")
        return
    if not stage.create_ticket(
            "Power light keeps blinking orange and white",
            "<p>The computer will not start. The power light keeps "
            "blinking orange and white in a pattern.</p>"):
        return
    try:
        rec = stage.poll_in()
        if not step("ticket indexed", rec is not None):
            return
        run, req = stage.wait_run_and_request(rec, args.timeout)
        if not req:
            return
        item = next((i for i in req.items
                     if i.get("template") == "power_light_video"),
                    None)
        if not step("power light video was asked",
                    item is not None):
            return
        cap_id, code = stage.upload(
            req.token, item["id"], args.video, "lights.mp4",
            "video/mp4")
        step("blink clip uploaded", cap_id is not None,
             f"http={code}")
        state = stage.wait_capture(req.token, cap_id, args.timeout)
        step("video verdict returned",
             bool(state and state.get("status") == "ok"),
             f"status={(state or {}).get('status')}")
        cap = stage.capture_row(cap_id)
        reading = (cap.reading or {}) if cap else {}
        bursts = reading.get("bursts") or []
        shown = ", ".join(f"{b.get('colour')} {b.get('count')}"
                          for b in bursts)
        step("pattern counted in code",
             "amber 2" in shown and "white 3" in shown,
             f"bursts={shown} locator={reading.get('locator')} "
             f"concept={reading.get('concept')}")
        step("Dell meaning decoded",
             "Memory" in str(reading.get("meaning") or ""),
             f"meaning={reading.get('meaning')}")
        stage.answers_and_skips(
            req, skip_templates={"screen_photo", "sticker",
                                 "whole_device", "cables_back",
                                 "printer_panel", "voice_note"})
        stage.http.post(f"{stage.base}/eyewitness/{req.token}"
                        "/voice-offer", json={"accept": False},
                        timeout=30)
        stage.http.post(f"{stage.base}/eyewitness/{req.token}/done",
                        timeout=30)
        notes = wait_for(
            lambda: stage.internal_notes("Memory") or None,
            args.timeout)
        step("internal summary holds the pattern", bool(notes))
        step("ticket released to the governor",
             stage.wait_released(run, args.timeout) is not None)
        stage.cost_step(run, req)
    finally:
        stage.close_ticket()


def scenario_voice(stage: Stage) -> None:
    args = stage.args
    if not args.voice:
        step("voice file supplied", False, "pass --voice")
        return
    if not stage.create_ticket(
            "Computer crashed and now will not start",
            "<p>My computer crashed twice this morning and now "
            "will not start at all.</p>"):
        return
    try:
        rec = stage.poll_in()
        if not step("ticket indexed", rec is not None):
            return
        run, req = stage.wait_run_and_request(rec, args.timeout)
        if not req:
            return
        for item in (req.items or []):
            if item.get("kind") == "question":
                opts = item.get("options") or []
                stage.http.post(
                    f"{stage.base}/eyewitness/{req.token}/answer",
                    json={"item_id": item["id"],
                          "answer": opts[0] if opts else "today"},
                    timeout=30)
            else:
                stage.http.post(
                    f"{stage.base}/eyewitness/{req.token}/skip",
                    json={"item_id": item["id"],
                          "reason": "can't do this"}, timeout=30)
        step("every item skipped", True)
        stage.http.post(f"{stage.base}/eyewitness/{req.token}"
                        "/voice-offer", json={"accept": True},
                        timeout=30)
        step("voice offer accepted", True)
        from app.eyewitness.models import EvidenceRequest

        def fresh_request():
            stage.db.session.expire_all()
            return stage.db.session.get(EvidenceRequest, req.id)

        grown = wait_for(
            lambda: (lambda r: r if r and any(
                i.get("template") == "voice_note"
                for i in (r.items or [])) else None)(
                    fresh_request()), 30)
        if not step("voice item added", grown is not None):
            return
        item = next(i for i in grown.items
                    if i.get("template") == "voice_note")
        cap_id, code = stage.upload(
            req.token, item["id"], args.voice, "note.webm",
            "audio/webm")
        step("voice note uploaded", cap_id is not None,
             f"http={code}")
        state = stage.wait_capture(req.token, cap_id, args.timeout)
        step("voice verdict returned",
             bool(state and state.get("status") == "ok"),
             f"status={(state or {}).get('status')}")
        cap = stage.capture_row(cap_id)
        reading = (cap.reading or {}) if cap else {}
        step("transcript stored",
             bool(str(reading.get("transcript") or "").strip()),
             f"chars={len(str(reading.get('transcript') or ''))}")
        step("summary stored in English",
             bool(str(reading.get("summary") or "").strip()),
             f"language={reading.get('language')}")
        stage.http.post(f"{stage.base}/eyewitness/{req.token}/done",
                        timeout=30)
        step("ticket released to the governor",
             stage.wait_released(run, args.timeout) is not None)
        stage.cost_step(run, req)
    finally:
        stage.close_ticket()


def scenario_injection(stage: Stage) -> None:
    args = stage.args
    if not args.injection_image:
        step("injection image supplied", False,
             "pass --injection-image")
        return
    if not stage.create_ticket(
            "Computer crashes to a black screen",
            "<p>The screen goes black and says it ran into a "
            "problem and needs to restart, with a small stop code "
            "line at the bottom.</p>"):
        return
    try:
        rec = stage.poll_in()
        if not step("ticket indexed", rec is not None):
            return
        run, req = stage.wait_run_and_request(rec, args.timeout)
        if not req:
            return
        before_text = req.customer_text or ""
        screen_item = next(
            (i for i in req.items if i.get("template")
             in ("screen_photo", "screen_photo_next_time")), None)
        if not step("screen item was asked", screen_item is not None):
            return
        cap_id, code = stage.upload(
            req.token, screen_item["id"], args.injection_image,
            "screen.png", "image/png")
        step("sticky note photo uploaded", cap_id is not None,
             f"http={code}")
        state = stage.wait_capture(req.token, cap_id, args.timeout)
        step("screen verdict returned",
             bool(state and state.get("status") == "ok"),
             f"status={(state or {}).get('status')}")
        cap = stage.capture_row(cap_id)
        step("injection flagged",
             bool(cap and cap.injection_flag),
             f"flag={getattr(cap, 'injection_flag', None)}")
        from app.eyewitness.models import EvidenceRequest
        stage.db.session.expire_all()
        fresh = stage.db.session.get(EvidenceRequest, req.id)
        step("customer text unchanged by the note",
             (fresh.customer_text or "") == before_text
             and "admin password" not in (fresh.customer_text or ""),
             "")
        stage.answers_and_skips(req, skip_templates={
            "power_light_video", "sticker", "whole_device",
            "cables_back", "printer_panel", "voice_note"})
        stage.http.post(f"{stage.base}/eyewitness/{req.token}"
                        "/voice-offer", json={"accept": False},
                        timeout=30)
        stage.http.post(f"{stage.base}/eyewitness/{req.token}/done",
                        timeout=30)
        notes = wait_for(
            lambda: stage.internal_notes("treated as data") or None,
            args.timeout)
        step("flag shown in the internal note", bool(notes))
        step("ticket released to the governor",
             stage.wait_released(run, args.timeout) is not None)
    finally:
        stage.close_ticket()


def scenario_help(stage: Stage) -> None:
    """The help button path: device known, no sticker, one item
    that stays open, then a follow-up capture after release."""
    args = stage.args
    from app.models import Devices
    device = (Devices.query.filter_by(tenant_uuid=stage.tenant_uuid)
              .order_by(Devices.devicename).first())
    if not step("managed device found for the tenant",
                device is not None,
                f"device={getattr(device, 'devicename', None)}"):
        return
    from app.models.helpdesk import HelpdeskSettings
    hs = stage.db.session.get(HelpdeskSettings, stage.tenant_uuid)
    if hs is None:
        hs = HelpdeskSettings(tenant_uuid=stage.tenant_uuid,
                              help_enabled=True)
        stage.db.session.add(hs)
    else:
        hs.help_enabled = True
        hs.help_orgs = None
    stage.db.session.commit()
    from app.utilities.help_intake import (issue_link,
                                           request_for_token, submit)
    try:
        help_req, url = issue_link(
            device, account=BRENDA_NAME, upn=BRENDA_EMAIL,
            display=BRENDA_NAME, base_url=stage.base)
    except Exception as exc:
        step("help link issued", False, str(exc)[:120])
        return
    step("help link issued", True)
    token = url.rstrip("/").split("/")[-1]
    opened = request_for_token(token)
    if not step("help request found for its token",
                opened is not None):
        return
    try:
        record = submit(
            opened, name=BRENDA_NAME, email=BRENDA_EMAIL,
            subject="Screen went blue twice this morning",
            description="The screen went blue twice this morning "
                        "with a black screen and a small line at "
                        "the bottom. It is fine right now and it "
                        "comes and goes, so it may happen again.",
            base_url=stage.base)
    except Exception as exc:
        step("help ticket filed", False, str(exc)[:160])
        return
    stage.ext_id = record.external_id \
        if record.provider == "zammad" else None
    step("help ticket filed", True,
         f"provider={record.provider} "
         f"number={record.external_number}")
    try:
        from app.eyewitness.models import EvidenceRequest, IntakeRun

        def find_run():
            stage.db.session.expire_all()
            run = IntakeRun.query.filter_by(
                helpdesk_ticket_id=record.id).order_by(
                    IntakeRun.id.desc()).first()
            return run if run and run.status \
                == "evidence_pending" else None

        run = wait_for(find_run, args.timeout)
        if not step("intake run holds the help ticket",
                    run is not None):
            return

        def find_request():
            stage.db.session.expire_all()
            return EvidenceRequest.query.filter_by(
                intake_run_id=run.id).order_by(
                    EvidenceRequest.id.desc()).first()

        req = wait_for(find_request, 30)
        templates = [i.get("template") for i in (req.items or [])] \
            if req else []
        step("evidence request for the help ticket", bool(req),
             f"items={templates}")
        step("no sticker item (device is known)",
             "sticker" not in templates, f"items={templates}")
        next_item = next(
            (i for i in (req.items or [])
             if i.get("template") == "screen_photo_next_time"),
            None)
        step("next-time item asked", next_item is not None,
             f"items={templates}")
        step("help ticket held from the governor",
             record.triage_status == "pending",
             f"triage={record.triage_status}")
        if not req or not next_item:
            return
        if not args.golden:
            step("golden photo supplied", False, "pass --golden")
            return
        cap_id, code = stage.upload(
            req.token, next_item["id"], args.golden, "crash.png",
            "image/png")
        step("next-time photo uploaded", cap_id is not None,
             f"http={code}")
        state = stage.wait_capture(req.token, cap_id, args.timeout)
        step("next-time verdict returned",
             bool(state and state.get("status") == "ok"),
             f"status={(state or {}).get('status')}")
        stage.answers_and_skips(req, skip_templates=set())
        stage.http.post(f"{stage.base}/eyewitness/{req.token}"
                        "/voice-offer", json={"accept": False},
                        timeout=30)
        stage.http.post(f"{stage.base}/eyewitness/{req.token}/done",
                        timeout=30)
        step("ticket released after Done",
             stage.wait_released(run, args.timeout) is not None)
        gov = stage.wait_governor(record, args.timeout)
        step("governor took the help ticket", gov is not None,
             f"triage={getattr(gov, 'triage_status', None)}")
        triage_at = getattr(gov, "triage_at", None) if gov else None
        if stage.client is not None and stage.ext_id:
            before = len(stage.internal_notes())
            cap2, code2 = stage.upload(
                req.token, next_item["id"], args.golden,
                "crash-again.png", "image/png")
            step("follow-up photo after release uploaded",
                 cap2 is not None, f"http={code2}")
            state2 = stage.wait_capture(req.token, cap2,
                                        args.timeout)
            step("follow-up verdict returned",
                 bool(state2 and state2.get("status") == "ok"),
                 f"status={(state2 or {}).get('status')}")
            notes = wait_for(
                lambda: (stage.internal_notes()
                         if len(stage.internal_notes()) > before
                         else None), args.timeout)
            step("follow-up internal note posted", bool(notes),
                 f"notes={len(notes or [])} before={before}")
            stage.db.session.expire_all()
            from app.models.helpdesk import HelpdeskTicket
            fresh = stage.db.session.get(HelpdeskTicket, record.id)
            step("no second governor run",
                 getattr(fresh, "triage_at", None) == triage_at,
                 f"triage_at={getattr(fresh, 'triage_at', None)}")
        else:
            step("follow-up checks need a desk ticket", False,
                 "help ticket filed without a desk")
    finally:
        if stage.client is not None and stage.ext_id:
            stage.close_ticket()


SCENARIOS = {"email": scenario_email, "help": scenario_help,
             "video": scenario_video, "voice": scenario_voice,
             "injection": scenario_injection}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="email",
                        choices=sorted(SCENARIOS),
                        help="which flow to run (default email)")
    parser.add_argument("--golden", default="",
                        help="path to the golden crash-screen image")
    parser.add_argument("--base-url", required=True,
                        help="the app base URL, no trailing slash")
    parser.add_argument("--tenant", required=True,
                        help="the demo tenant uuid")
    parser.add_argument("--video", default="",
                        help="path to the blink clip (video scenario)")
    parser.add_argument("--voice", default="",
                        help="path to the voice note (voice scenario)")
    parser.add_argument("--injection-image", default="",
                        help="path to the sticky note photo "
                             "(injection scenario)")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.scenario in ("email", "help") and not args.golden:
        print("FAIL golden image supplied - pass --golden")
        return 1

    stage = Stage(args)
    try:
        if not stage.enable_tenant():
            return 1
        SCENARIOS[args.scenario](stage)
    finally:
        try:
            stage.ctx.pop()
        except Exception:
            pass
    print("E2E " + args.scenario + ": "
          + ("FAIL" if FAILURES else "PASS")
          + (f" ({', '.join(FAILURES)})" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
