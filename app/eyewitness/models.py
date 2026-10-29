"""Eyewitness tables (brief section 7.1). Epoch-second timestamps and
JSONB, matching the house style in app/models/helpdesk.py."""
import time

from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models import db

RUN_ASSESSING = "assessing"
RUN_NO_REQUEST = "no_request"
RUN_EVIDENCE_PENDING = "evidence_pending"
RUN_RELEASED = "released"
RUN_FAILED = "failed"

REQ_NEEDS_APPROVAL = "needs_approval"
REQ_OPEN = "open"
REQ_SUBMITTED = "submitted"
REQ_EXPIRED = "expired"
REQ_CANCELLED = "cancelled"

CAP_UPLOADED = "uploaded"
CAP_PROCESSING = "processing"
CAP_OK = "ok"
CAP_RETAKE = "retake"
CAP_FAILED = "failed"
CAP_SKIPPED = "skipped"

VOICE_NOT_SHOWN = "not_shown"
VOICE_SHOWN = "shown"
VOICE_ACCEPTED = "accepted"
VOICE_DECLINED = "declined"


def _now() -> int:
    return int(time.time())


class IntakeRun(db.Model):
    __tablename__ = "eyewitness_intake_runs"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    tenant_uuid = db.Column(UUID(as_uuid=True),
                            db.ForeignKey("tenants.tenantuuid", ondelete="CASCADE"),
                            nullable=False, index=True)
    helpdesk_ticket_id = db.Column(
        db.BigInteger,
        db.ForeignKey("helpdesk_tickets.id", ondelete="CASCADE"),
        nullable=False, index=True)
    deviceuuid = db.Column(UUID(as_uuid=True),
                           db.ForeignKey("devices.deviceuuid", ondelete="SET NULL"),
                           nullable=True)
    origin = db.Column(db.String(16), nullable=False)
    status = db.Column(db.String(24), nullable=False, default=RUN_ASSESSING)
    assessment = db.Column(JSONB, nullable=True)
    model = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.BigInteger, nullable=False, default=_now)
    released_at = db.Column(db.BigInteger, nullable=True)
    error = db.Column(db.Text, nullable=True)


class EvidenceRequest(db.Model):
    __tablename__ = "eyewitness_requests"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    tenant_uuid = db.Column(UUID(as_uuid=True),
                            db.ForeignKey("tenants.tenantuuid", ondelete="CASCADE"),
                            nullable=False, index=True)
    intake_run_id = db.Column(
        db.BigInteger,
        db.ForeignKey("eyewitness_intake_runs.id", ondelete="SET NULL"),
        nullable=True)
    # Nullable since Phase 5: a request created through the Evidence API
    # for an external desk has no HelpdeskTicket row until that desk is
    # polled or pushed in. Desk-born requests always carry one.
    helpdesk_ticket_id = db.Column(
        db.BigInteger,
        db.ForeignKey("helpdesk_tickets.id", ondelete="CASCADE"),
        nullable=True, index=True)
    source = db.Column(db.String(16), nullable=True, default="desk")
    external_ref = db.Column(db.String(200), nullable=True)
    api_key_id = db.Column(
        db.BigInteger,
        db.ForeignKey("eyewitness_api_keys.id", ondelete="SET NULL"),
        nullable=True, index=True)
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    items = db.Column(JSONB, nullable=False, default=list)
    customer_text = db.Column(db.Text, nullable=True)
    reason_for_technician = db.Column(db.Text, nullable=True)
    language = db.Column(db.String(8), nullable=True)
    status = db.Column(db.String(20), nullable=False, default=REQ_OPEN)
    approved_by = db.Column(UUID(as_uuid=True), nullable=True)
    approved_at = db.Column(db.BigInteger, nullable=True)
    filter_hits = db.Column(JSONB, nullable=True)
    delivered_via = db.Column(JSONB, nullable=True)
    created_at = db.Column(db.BigInteger, nullable=False, default=_now)
    expires_at = db.Column(db.BigInteger, nullable=False)
    opened_at = db.Column(db.BigInteger, nullable=True)
    submitted_at = db.Column(db.BigInteger, nullable=True)
    voice_offer = db.Column(db.String(16), nullable=False,
                            default=VOICE_NOT_SHOWN)
    purged_at = db.Column(db.BigInteger, nullable=True)

    @property
    def is_open(self) -> bool:
        return self.status == REQ_OPEN and int(time.time()) < int(self.expires_at)


class Capture(db.Model):
    __tablename__ = "eyewitness_captures"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    request_id = db.Column(
        db.BigInteger,
        db.ForeignKey("eyewitness_requests.id", ondelete="CASCADE"),
        nullable=False, index=True)
    item_id = db.Column(db.String(64), nullable=False)
    kind = db.Column(db.String(24), nullable=False)
    file_path = db.Column(db.String(500), nullable=True)
    mime = db.Column(db.String(80), nullable=True)
    size = db.Column(db.BigInteger, nullable=True)
    sha256 = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(16), nullable=False, default=CAP_UPLOADED)
    skip_reason = db.Column(db.String(200), nullable=True)
    quality = db.Column(JSONB, nullable=True)
    reading = db.Column(JSONB, nullable=True)
    retake = db.Column(JSONB, nullable=True)
    injection_flag = db.Column(db.Boolean, nullable=False, default=False)
    prompt_version = db.Column(db.String(32), nullable=True)
    created_at = db.Column(db.BigInteger, nullable=False, default=_now)
    processed_at = db.Column(db.BigInteger, nullable=True)
    purged_at = db.Column(db.BigInteger, nullable=True)
    error = db.Column(db.Text, nullable=True)


class MetaCall(db.Model):
    __tablename__ = "eyewitness_meta_calls"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    intake_run_id = db.Column(
        db.BigInteger,
        db.ForeignKey("eyewitness_intake_runs.id", ondelete="SET NULL"),
        nullable=True)
    capture_id = db.Column(
        db.BigInteger,
        db.ForeignKey("eyewitness_captures.id", ondelete="SET NULL"),
        nullable=True)
    kind = db.Column(db.String(16), nullable=False)
    model = db.Column(db.String(80), nullable=True)
    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    cached_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
    units = db.Column(db.Integer, nullable=False, default=0)
    usd = db.Column(db.Float, nullable=False, default=0.0)
    cache_hit = db.Column(db.Boolean, nullable=False, default=False)
    ms = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.BigInteger, nullable=False, default=_now)


class MetaCache(db.Model):
    __tablename__ = "eyewitness_meta_cache"

    key = db.Column(db.String(64), primary_key=True)
    response = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.BigInteger, nullable=False, default=_now)


class TenantSettings(db.Model):
    """Per-tenant Eyewitness switches. Absence means off (M1 opt-in)."""
    __tablename__ = "eyewitness_tenant_settings"

    tenant_uuid = db.Column(UUID(as_uuid=True),
                            db.ForeignKey("tenants.tenantuuid", ondelete="CASCADE"),
                            primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    auto_send_templates = db.Column(db.Boolean, nullable=False, default=True)
    # Phase 5 webhook identity. The hook id is the public half of the
    # Zammad push address. The HMAC secret lives in the secret store
    # under eyewitness_webhook_secret:<tenant_uuid> and never here.
    webhook_hook_id = db.Column(db.String(64), nullable=True, unique=True,
                                index=True)
    # Phase 6: where this tenant's evidence is read. ``meta`` sends
    # model calls to the Meta Model API. ``local`` sends them to Muse
    # Glimmer on the tenant's own hardware and never to the cloud.
    reader_backend = db.Column(db.String(16), nullable=False, default="meta",
                               server_default="meta")
    updated_at = db.Column(db.BigInteger, nullable=False, default=_now,
                           onupdate=_now)


class ApiKey(db.Model):
    """One Evidence API key for one tenant (Phase 5).

    The plaintext key is shown once at creation. Only its SHA-256 hash
    and a short visible prefix stay in this table. The callback secret,
    where a callback URL is set, lives in the secret store under
    eyewitness_callback_secret:<id> and never here.
    """
    __tablename__ = "eyewitness_api_keys"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    tenant_uuid = db.Column(UUID(as_uuid=True),
                            db.ForeignKey("tenants.tenantuuid", ondelete="CASCADE"),
                            nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="")
    key_prefix = db.Column(db.String(16), nullable=False, index=True)
    key_hash = db.Column(db.String(64), nullable=False)
    scopes = db.Column(JSONB, nullable=False, default=list)
    callback_url = db.Column(db.String(500), nullable=True)
    last_used_at = db.Column(db.BigInteger, nullable=True)
    revoked_at = db.Column(db.BigInteger, nullable=True)
    created_at = db.Column(db.BigInteger, nullable=False, default=_now)
    created_by = db.Column(UUID(as_uuid=True), nullable=True)


class CallbackDelivery(db.Model):
    """One signed callback attempt set for one API request (Phase 5)."""
    __tablename__ = "eyewitness_callback_deliveries"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    api_key_id = db.Column(
        db.BigInteger,
        db.ForeignKey("eyewitness_api_keys.id", ondelete="CASCADE"),
        nullable=False, index=True)
    request_id = db.Column(
        db.BigInteger,
        db.ForeignKey("eyewitness_requests.id", ondelete="CASCADE"),
        nullable=False, index=True)
    event = db.Column(db.String(32), nullable=False)
    payload = db.Column(JSONB, nullable=True)
    status = db.Column(db.String(16), nullable=False, default="pending")
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.BigInteger, nullable=False, default=_now)
    delivered_at = db.Column(db.BigInteger, nullable=True)
    next_attempt_at = db.Column(db.BigInteger, nullable=True)
