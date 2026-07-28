import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

# Dedupe: skip outgoing webhooks for messages we just sent (Odoo→Chatwoot poll)
# Prevents duplicate in Odoo when Chatwoot echoes our send back via webhook.
_RECENTLY_SENT_CACHE: Dict[Tuple[int, str], float] = {}
ECHO_DEDUP_WINDOW = 120  # seconds


def _record_sent_to_chatwoot(conv_id: int, content: str) -> None:
    """Record that we sent this message to Chatwoot (to skip echo webhook)."""
    key = (conv_id, (content or "").strip())
    _RECENTLY_SENT_CACHE[key] = time.time()
    # Prune old entries
    cutoff = time.time() - ECHO_DEDUP_WINDOW
    for k in list(_RECENTLY_SENT_CACHE):
        if _RECENTLY_SENT_CACHE[k] < cutoff:
            del _RECENTLY_SENT_CACHE[k]


def _we_recently_sent_this(conv_id: int, content: str) -> bool:
    """True if we sent this exact message to Chatwoot recently (echo)."""
    key = (conv_id, (content or "").strip())
    ts = _RECENTLY_SENT_CACHE.get(key)
    if ts is None:
        return False
    if time.time() - ts > ECHO_DEDUP_WINDOW:
        del _RECENTLY_SENT_CACHE[key]
        return False
    del _RECENTLY_SENT_CACHE[key]  # Consume so we don't block future same message
    return True

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _log_preview(text: Optional[str], max_len: int = 120) -> str:
    """Short one-line preview for logs (no newlines)."""
    s = (text or "").replace("\n", " ").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


from discuss_mapping import (
    bump_last_message_id_if_newer,
    get_channel_for_conversation,
    get_all_conversation_mappings,
    get_last_message_id,
    migrate_conversation_id,
    remove_channel_for_conversation,
    set_channel_for_conversation,
    set_last_message_id,
)
from odoo_client import get_odoo_client, strip_html

# Configuration
RECEEVI_URL = (os.getenv("RECEEVI_URL", "http://receevi-web:3000") or "").rstrip("/")
# Chamadas server-side (poll Odoo→Chatwoot) devem usar o hostname Docker/Swarm (ex.: receevi-web:3000).
# RECEEVI_URL público (https://oc...) dentro do contentor falha muitas vezes (hairpin, DNS, TLS).
RECEEVI_INTERNAL_URL = (os.getenv("RECEEVI_INTERNAL_URL") or "").rstrip("/")
CHATWOOT_API_BASE = RECEEVI_INTERNAL_URL or RECEEVI_URL
RECEEVI_API_KEY = os.getenv("RECEEVI_API_KEY", "")
RECEEVI_ACCOUNT_ID = os.getenv("RECEEVI_ACCOUNT_ID", "1")
RECEEVI_SMS_INBOX_ID = os.getenv("RECEEVI_SMS_INBOX_ID", "")
ODOO_URL = os.getenv("ODOO_URL", "http://odoo:8069")
ODOO_DB = os.getenv("ODOO_DB", "odoo")
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "admin")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "admin")
DISCUSS_POLL_INTERVAL = int(os.getenv("DISCUSS_POLL_INTERVAL", "5"))  # seconds

# Hostnames onde o Chatwoot (Puma) corre só em HTTP na rede Docker; Rails pode devolver
# redirects 308 para https://receevi-web:3000 → TLS falha ("SSL record layer failure").
CHATWOOT_INTERNAL_HTTP_HOSTS = frozenset(
    h.strip().lower()
    for h in os.getenv("CHATWOOT_INTERNAL_HTTP_HOSTS", "receevi-web").split(",")
    if h.strip()
)


def _normalize_chatwoot_url(url: str) -> str:
    """Força http em hosts internos quando Rails redireciona para https inexistente no :3000."""
    try:
        p = urlparse(url)
    except Exception:
        return url
    host = (p.hostname or "").lower()
    if host in CHATWOOT_INTERNAL_HTTP_HOSTS and p.scheme == "https":
        return urlunparse(p._replace(scheme="http"))
    return url


def _chatwoot_headers_for_url(url: str, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Rails/Chatwoot com force_ssl responde 308 http→https mesmo no Puma sem TLS.
    X-Forwarded-Proto faz o Rack tratar o pedido como já em HTTPS e não redirecionar.
    """
    h = dict(base or {})
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    if host in CHATWOOT_INTERNAL_HTTP_HOSTS:
        h.setdefault("X-Forwarded-Proto", "https")
        h.setdefault("X-Forwarded-Ssl", "on")
    return h


async def _chatwoot_request(
    method: str,
    url: str,
    *,
    timeout: float = 30.0,
    **kwargs: Any,
) -> httpx.Response:
    """
    Pedido à API Chatwoot com redirects manuais.
    Hosts internos: headers X-Forwarded-Proto para evitar 308 http↔https em ciclo.
    """
    url = _normalize_chatwoot_url(url)
    method_u = method.upper()
    headers_base = dict(kwargs.pop("headers", None) or {})
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        redirect_codes = frozenset({301, 302, 303, 307, 308})
        for _ in range(16):
            hdrs = _chatwoot_headers_for_url(url, headers_base)
            resp = await client.request(method_u, url, headers=hdrs, **kwargs)
            if resp.status_code not in redirect_codes:
                return resp
            loc = resp.headers.get("location")
            if not loc:
                return resp
            next_url = _normalize_chatwoot_url(urljoin(str(resp.url), loc))
            # Sem X-Forwarded-Proto, Location https→normalize http fica igual ao URL atual;
            # não devolver o 308: seguir para o mesmo URL já com hdrs acima (ou avançar).
            if next_url == url:
                return resp
            url = next_url
            if resp.status_code == 303 and method_u == "POST":
                method_u = "GET"
                kwargs = {
                    k: v
                    for k, v in kwargs.items()
                    if k not in ("json", "data", "content", "files")
                }
        return resp


# Background task control
_poll_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start/stop Discuss polling background task."""
    global _poll_task
    if RECEEVI_API_KEY:
        logger.info(
            "Odoo→Chatwoot poll started (account_id=%s, chatwoot_api=%s). Agent replies will sync.",
            RECEEVI_ACCOUNT_ID,
            CHATWOOT_API_BASE,
        )
    else:
        logger.warning(
            "RECEEVI_API_KEY not set. Odoo→Chatwoot sync disabled. "
            "Add RECEEVI_API_KEY to .env and restart FastAPI."
        )
    _poll_task = asyncio.create_task(_poll_odoo_to_receevi())
    yield
    if _poll_task:
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Omnichannel Integration API",
    description="FastAPI service integrating Receevi, Odoo Discuss, and SMS providers",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request so we can confirm Traefik is routing to this service."""
    logger.info("Request %s %s", request.method, request.url.path)
    response = await call_next(request)
    return response


# Pydantic Models
class ReceeviWebhook(BaseModel):
    """Receevi webhook payload"""
    event: str
    account: Optional[Dict[str, Any]] = None
    conversation: Optional[Dict[str, Any]] = None
    message: Optional[Dict[str, Any]] = None
    sender: Optional[Dict[str, Any]] = None

class OdooTicket(BaseModel):
    """Odoo helpdesk ticket"""
    name: str
    description: str
    partner_name: Optional[str] = None
    partner_email: Optional[str] = None
    partner_phone: Optional[str] = None
    channel: str = "chat"

class SMSMessage(BaseModel):
    """SMS message"""
    to: str
    message: str
    from_number: Optional[str] = None


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "service": "Omnichannel Integration API",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/health")
async def health_check():
    """Detailed health check"""
    health_status = {
        "api": "healthy",
        "receevi": "unknown",
        "odoo": "unknown",
        "timestamp": datetime.now().isoformat()
    }
    
    # Check Receevi (Rails app can take 1–2 min to boot)
    try:
        response = await _chatwoot_request("GET", f"{CHATWOOT_API_BASE}/api", timeout=5.0)
        health_status["receevi"] = "healthy" if response.status_code == 200 else "unhealthy"
    except Exception as e:
        health_status["receevi"] = f"error: {str(e)}"
    
    # Check Odoo
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{ODOO_URL}/web/health")
            health_status["odoo"] = "healthy" if response.status_code == 200 else "unhealthy"
    except Exception as e:
        health_status["odoo"] = f"error: {str(e)}"
    
    return health_status


@app.post("/debug/resolve-mapping")
async def debug_resolve_mapping():
    """
    One-time fix: resolve display_ids in mapping to internal conversation ids.
    Call this if Odoo→Chatwoot send fails with 'undefined method name for nil'.
    """
    if not RECEEVI_API_KEY:
        return {"error": "RECEEVI_API_KEY not set"}
    mappings = get_all_conversation_mappings()
    migrated = []
    for conv_id, data in list(mappings.items()):
        resolved = await resolve_display_id_to_conversation_id(
            display_id=conv_id,
            inbox_id=None,
        )
        if resolved is not None and resolved != conv_id:
            migrate_conversation_id(conv_id, resolved)
            migrated.append({"from": conv_id, "to": resolved})
    return {"migrated": migrated, "mappings_count": len(mappings)}


@app.post("/debug/fix-mapping")
async def debug_fix_mapping(
    old_conv_id: int = Query(..., description="Current conv_id in mapping (e.g. 4)"),
    new_conv_id: int = Query(..., description="Correct Chatwoot conversation id from URL (e.g. 6)"),
):
    """
    Manually migrate mapping from old_conv_id to new_conv_id.
    Use when Chatwoot URL shows conversations/X but mapping has Y.
    Example: POST /debug/fix-mapping?old_conv_id=4&new_conv_id=6
    """
    ok = migrate_conversation_id(old_conv_id, new_conv_id)
    return {"migrated": ok, "from": old_conv_id, "to": new_conv_id}


@app.get("/webhooks/receevi")
async def receevi_webhook_get():
    """GET: confirm webhook endpoint is active. Receevi sends POST with message_created payload."""
    return {
        "status": "ok",
        "message": "Receevi webhook endpoint. Use POST with message_created events.",
        "configure_in_receevi": "Settings → Integrations → Webhooks → URL: http://fastapi:8000/webhooks/receevi",
    }


def _parse_chatwoot_incoming_payload(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parse Chatwoot webhook for INCOMING messages (from contact).
    Returns dict with: conv_id, content, partner_*, or None if skip.
    """
    event = body.get("event")
    if event != "message_created":
        return None

    msg_type = body.get("message_type") or (body.get("message") or {}).get("message_type")
    if msg_type == "outgoing":
        return None

    # Content: top-level (Chatwoot) or nested (Receevi)
    content = body.get("content") or (body.get("message") or {}).get("content") or ""

    # Conversation id: Chatwoot API requires internal id (not display_id).
    # Prefer id; fallback to display_id only if id is missing (we'll resolve via API).
    conv = body.get("conversation") or (body.get("message") or {}).get("conversation") or {}
    raw_id = conv.get("id")
    display_id = conv.get("display_id")
    # inbox_id / inbox_name: from conversation or top-level "inbox" (Chatwoot sends inbox.id + inbox.name)
    inbox_id = conv.get("inbox_id")
    inbox = body.get("inbox") or (body.get("message") or {}).get("inbox") or {}
    if inbox and inbox_id is None:
        inbox_id = inbox.get("id")
    inbox_name = inbox.get("name") if inbox else None
    if isinstance(inbox_name, str):
        inbox_name = inbox_name.strip() or None
    conv_id = None
    conv_id_from_display = False
    if raw_id is not None:
        conv_id = int(raw_id) if isinstance(raw_id, str) and str(raw_id).isdigit() else raw_id
    elif display_id is not None:
        conv_id = int(display_id) if isinstance(display_id, str) and str(display_id).isdigit() else display_id
        conv_id_from_display = True
    if not conv_id:
        return None

    # Contact/sender: for incoming, use contact (Chatwoot); for Receevi use sender
    contact = body.get("contact") or {}
    sender = body.get("sender") or {}
    partner_name = contact.get("name") or sender.get("name")
    partner_email = contact.get("email") or sender.get("email")
    partner_phone = contact.get("identifier") or contact.get("phone_number") or sender.get("phone_number")

    # Contact identifier (phone/email) - Chatwoot may use identifier or custom_attributes
    if not partner_phone and not partner_email:
        partner_phone = contact.get("phone_number") or (contact.get("custom_attributes") or {}).get("phone_number")
        partner_email = contact.get("email") or (contact.get("custom_attributes") or {}).get("email")

    return {
        "conv_id": conv_id,
        "conv_id_from_display": conv_id_from_display,
        "inbox_id": inbox_id,
        "inbox_name": inbox_name,
        "content": content,
        "partner_name": partner_name or "Contact",
        "partner_email": partner_email,
        "partner_phone": partner_phone,
    }


def _parse_chatwoot_outgoing_payload(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parse Chatwoot webhook for OUTGOING messages (agent replies from Chatwoot).
    Returns dict with: conv_id, content, or None if skip.
    """
    event = body.get("event")
    if event != "message_created":
        return None

    msg_type = body.get("message_type") or (body.get("message") or {}).get("message_type")
    if msg_type != "outgoing":
        return None

    content = body.get("content") or (body.get("message") or {}).get("content") or ""
    if not content.strip():
        return None

    conv = body.get("conversation") or (body.get("message") or {}).get("conversation") or {}
    raw_id = conv.get("id")
    display_id = conv.get("display_id")
    inbox_id = conv.get("inbox_id")
    conv_id = None
    conv_id_from_display = False
    if raw_id is not None:
        conv_id = int(raw_id) if isinstance(raw_id, str) and str(raw_id).isdigit() else raw_id
    elif display_id is not None:
        conv_id = int(display_id) if isinstance(display_id, str) and str(display_id).isdigit() else display_id
        conv_id_from_display = True
    if not conv_id:
        return None

    return {
        "conv_id": conv_id,
        "conv_id_from_display": conv_id_from_display,
        "inbox_id": inbox_id,
        "content": content,
    }


@app.post("/webhooks/receevi")
async def receevi_webhook(request: Request):
    """
    Receive webhooks from Receevi (Chatwoot) and sync to Odoo Discuss.
    Incoming messages from WhatsApp/Website/SMS → Odoo Discuss chat.
    Supports both Chatwoot flat payload and Receevi nested payload.
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.warning("Webhook invalid JSON: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON")
    event = body.get("event", "?")
    logger.info("Webhook received from Receevi: event=%s", event)

    # Try incoming (contact) message first
    parsed = _parse_chatwoot_incoming_payload(body)
    if parsed:
        conv_id = parsed["conv_id"]
        if parsed.get("conv_id_from_display"):
            resolved = await resolve_display_id_to_conversation_id(
                display_id=conv_id,
                inbox_id=parsed.get("inbox_id"),
            )
            if resolved is not None:
                migrate_conversation_id(conv_id, resolved)
                conv_id = resolved
                logger.info("Resolved display_id to conversation id=%s", conv_id)
            else:
                logger.warning(
                    "Could not resolve display_id=%s to conversation id; API send may fail",
                    parsed["conv_id"],
                )

        try:
            inbox_id = parsed.get("inbox_id")
            if inbox_id is not None and isinstance(inbox_id, str) and str(inbox_id).isdigit():
                inbox_id = int(inbox_id)
            content = (parsed.get("content") or "").strip()
            if content and content.startswith("<"):
                content = strip_html(content)
            logger.info(
                "Receevi→Odoo (contact): conv_id=%s inbox_id=%s partner=%s content_preview=%r",
                conv_id,
                inbox_id,
                parsed.get("partner_name") or parsed.get("partner_phone") or "?",
                _log_preview(content, 160),
            )
            result = await add_chatwoot_message_to_discuss(
                conversation_id=conv_id,
                content=content,
                partner_name=parsed.get("partner_name"),
                partner_email=parsed.get("partner_email"),
                partner_phone=parsed.get("partner_phone"),
                inbox_id=inbox_id,
                inbox_name=parsed.get("inbox_name"),
            )
        except Exception as e:
            logger.exception("Odoo Discuss error: %s", e)
            raise HTTPException(status_code=502, detail=str(e))

        if result.get("error"):
            logger.error(
                "Receevi→Odoo FAILED: conv_id=%s odoo_error=%s",
                conv_id,
                result["error"],
            )
            raise HTTPException(status_code=502, detail=result["error"])

        logger.info(
            "Receevi→Odoo OK: conv_id=%s odoo_channel_id=%s odoo_message_id=%s",
            conv_id,
            result.get("channel_id"),
            result.get("message_id"),
        )
        return {
            "status": "success",
            "channel_id": result.get("channel_id"),
            "message": "Message synced to Odoo Discuss",
        }

    # Try outgoing (agent) message - sync Chatwoot agent reply to Odoo
    parsed_out = _parse_chatwoot_outgoing_payload(body)
    if parsed_out:
        conv_id = parsed_out["conv_id"]
        content = parsed_out["content"]

        # Skip echo: we just sent this ourselves via Odoo→Chatwoot poll
        if _we_recently_sent_this(conv_id, content):
            return {"status": "ignored", "reason": "Echo of our own send (dedupe)"}

        if parsed_out.get("conv_id_from_display"):
            resolved = await resolve_display_id_to_conversation_id(
                display_id=conv_id,
                inbox_id=parsed_out.get("inbox_id"),
            )
            if resolved is not None:
                migrate_conversation_id(conv_id, resolved)
                conv_id = resolved

        logger.info(
            "Receevi→Odoo (agent): conv_id=%s content_preview=%r",
            conv_id,
            _log_preview(content, 160),
        )
        try:
            result = await add_chatwoot_agent_message_to_discuss(
                conversation_id=conv_id,
                content=content,
            )
        except Exception as e:
            logger.exception("Odoo Discuss error (agent): %s", e)
            raise HTTPException(status_code=502, detail=str(e))

        if result.get("error"):
            logger.error(
                "Receevi→Odoo (agent) FAILED: conv_id=%s odoo_error=%s",
                conv_id,
                result["error"],
            )
            raise HTTPException(status_code=502, detail=result["error"])

        logger.info(
            "Receevi→Odoo (agent) OK: conv_id=%s odoo_channel_id=%s odoo_message_id=%s",
            conv_id,
            result.get("channel_id"),
            result.get("message_id"),
        )
        return {
            "status": "success",
            "channel_id": result.get("channel_id"),
            "message": "Agent message synced to Odoo Discuss",
        }

    event = body.get("event", "?")
    if event != "message_created":
        return {"status": "ignored", "reason": f"Event {event} not processed"}
    return {"status": "ignored", "reason": "Missing conversation id or content"}


@app.post("/webhooks/sms/incoming")
async def receive_sms(request: Request):
    """
    Receive incoming SMS from providers (Twilio, Africa's Talking, etc.)
    """
    try:
        body = await request.form()
        
        # Extract SMS details (adjust based on your provider)
        from_number = body.get("From") or body.get("from")
        message_body = body.get("Body") or body.get("text")
        to_number = body.get("To") or body.get("to")
        
        if not from_number or not message_body:
            raise HTTPException(status_code=400, detail="Missing required fields")
        
        # Create conversation in Receevi (if SMS inbox and API key configured)
        inbox_id = RECEEVI_SMS_INBOX_ID
        conversation_result = {}
        if RECEEVI_API_KEY and inbox_id:
            conversation_result = await create_receevi_conversation({
                "account_id": RECEEVI_ACCOUNT_ID,
                "inbox_id": int(inbox_id),
                "source_id": from_number,
                "contact": {"identifier": from_number, "name": from_number},
            })
        
        # Send message to conversation
        if conversation_result.get("id"):
            await send_receevi_message(
                conversation_id=conversation_result["id"],
                content=message_body,
            )
        
        # Odoo Discuss sync happens via Receevi webhook when the message is created
        return {"status": "success", "message": "SMS processed"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sms/send")
async def send_sms(sms: SMSMessage):
    """
    Send SMS via Twilio or other provider
    """
    try:
        # This is a placeholder - implement based on your SMS provider
        # Example for Twilio:
        twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
        twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_from = os.getenv("TWILIO_FROM_NUMBER")
        
        if not all([twilio_sid, twilio_token, twilio_from]):
            raise HTTPException(
                status_code=500, 
                detail="Twilio credentials not configured"
            )
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json",
                auth=(twilio_sid, twilio_token),
                data={
                    "From": sms.from_number or twilio_from,
                    "To": sms.to,
                    "Body": sms.message
                }
            )
            
            if response.status_code == 201:
                return {"status": "success", "sid": response.json().get("sid")}
            else:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=response.text
                )
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/odoo/tickets")
async def create_ticket(ticket: OdooTicket):
    """
    Create a helpdesk ticket in Odoo
    """
    try:
        result = await create_odoo_ticket(ticket.model_dump())
        return {"status": "success", "ticket": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Helper Functions
async def create_odoo_ticket(ticket_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a ticket in Odoo via XML-RPC (helpdesk.ticket or project.task)."""
    client = get_odoo_client()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: client.create_helpdesk_ticket(ticket_data),
    )
    if result.get("error"):
        raise HTTPException(
            status_code=502,
            detail=f"Odoo: {result.get('error', 'Unknown error')}",
        )
    return {
        "id": result.get("id"),
        "name": ticket_data.get("name"),
        "status": result.get("status", "created"),
        "model": result.get("model"),
    }


async def create_receevi_conversation(conversation_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a conversation in Receevi (Chatwoot API)."""
    account_id = conversation_data.get("account_id") or RECEEVI_ACCOUNT_ID
    try:
        response = await _chatwoot_request(
            "POST",
            f"{CHATWOOT_API_BASE}/api/v1/accounts/{account_id}/conversations",
            headers={"api_access_token": RECEEVI_API_KEY},
            json=conversation_data,
        )
        if response.status_code in [200, 201]:
            return response.json() if response.content else {}
        return {"error": response.text, "id": None}
    except Exception as e:
        return {"error": str(e), "id": None}


async def resolve_display_id_to_conversation_id(
    display_id: int,
    inbox_id: Optional[int] = None,
) -> Optional[int]:
    """
    Resolve display_id to internal conversation id via Chatwoot API.
    Required when webhook only sends display_id (API expects internal id).
    """
    if not RECEEVI_API_KEY:
        return None
    try:
        params = {"status": "all", "page": 1}
        if inbox_id:
            params["inbox_id"] = inbox_id
        response = await _chatwoot_request(
            "GET",
            f"{CHATWOOT_API_BASE}/api/v1/accounts/{RECEEVI_ACCOUNT_ID}/conversations",
            headers={"api_access_token": RECEEVI_API_KEY},
            params=params,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        if isinstance(data, list):
            conversations = data
        elif isinstance(data, dict):
            payload = data.get("data") or data.get("payload") or data
            conversations = payload if isinstance(payload, list) else (payload.get("conversations", []) or [])
        else:
            conversations = []
        for c in conversations:
            cid = c.get("id")
            did = c.get("display_id")
            if did is not None and int(did) == int(display_id):
                return int(cid) if cid is not None else None
        return None
    except Exception as e:
        logger.warning("resolve_display_id_to_conversation_id failed: %s", e)
        return None


# Cache inbox_id -> name to avoid repeated Chatwoot API calls
_inbox_name_cache: Dict[int, str] = {}


async def get_inbox_name(inbox_id: Optional[int]) -> Optional[str]:
    """
    Resolve Chatwoot inbox_id to inbox name (e.g. WhatsApp, Website, SMS).
    Cached in memory for the process lifetime.
    """
    if inbox_id is None or not RECEEVI_API_KEY:
        return None
    inbox_id = int(inbox_id)
    if inbox_id in _inbox_name_cache:
        return _inbox_name_cache[inbox_id]
    try:
        response = await _chatwoot_request(
            "GET",
            f"{CHATWOOT_API_BASE}/api/v1/accounts/{RECEEVI_ACCOUNT_ID}/inboxes",
            headers={"api_access_token": RECEEVI_API_KEY},
        )
        if response.status_code != 200:
            return None
        data = response.json()
        payload = data.get("payload") if isinstance(data, dict) else data
        if not isinstance(payload, list):
            return None
        for inbox in payload:
            iid = inbox.get("id")
            name = inbox.get("name")
            if iid is not None and int(iid) == inbox_id and name:
                _inbox_name_cache[inbox_id] = str(name).strip()
                return _inbox_name_cache[inbox_id]
        return None
    except Exception as e:
        logger.warning("get_inbox_name failed for inbox_id=%s: %s", inbox_id, e)
        return None


async def send_receevi_message(
    conversation_id: int,
    content: str,
    message_type: str = "outgoing",
) -> Dict[str, Any]:
    """
    Send a message to a Receevi conversation.
    message_type: "outgoing" = agent reply, "incoming" = contact message
    """
    try:
        response = await _chatwoot_request(
            "POST",
            f"{CHATWOOT_API_BASE}/api/v1/accounts/{RECEEVI_ACCOUNT_ID}/conversations/{conversation_id}/messages",
            headers={"api_access_token": RECEEVI_API_KEY},
            json={
                "content": content,
                "message_type": message_type,
                "private": False,
                "content_type": "text",
            },
        )
        if response.status_code in [200, 201]:
            data = response.json() if response.content else {}
            cw_id = data.get("id") if isinstance(data, dict) else None
            logger.info(
                "Odoo→Receevi API OK: conv_id=%s message_type=%s chatwoot_message_id=%s",
                conversation_id,
                message_type,
                cw_id,
            )
            return data
        body = (response.text or "")[:500]
        logger.warning(
            "Odoo→Receevi API FAILED: conv_id=%s status=%s location=%r body_preview=%r url=%s",
            conversation_id,
            response.status_code,
            response.headers.get("location"),
            body,
            response.request.url,
        )
        return {"error": response.text or response.reason_phrase}
    except Exception as e:
        logger.warning(
            "Odoo→Receevi API exception: conv_id=%s err=%s",
            conversation_id,
            e,
        )
        return {"error": str(e)}


async def add_chatwoot_message_to_discuss(
    conversation_id: int,
    content: str,
    partner_name: Optional[str] = None,
    partner_email: Optional[str] = None,
    partner_phone: Optional[str] = None,
    inbox_id: Optional[int] = None,
    inbox_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Add a Chatwoot/Receevi message to Odoo Discuss.
    Creates or reuses a chat channel per conversation.
    Channel name includes inbox when inbox_name is provided or resolved from inbox_id (e.g. [Website] Chat #6 - Contact).
    """
    client = get_odoo_client()
    loop = asyncio.get_event_loop()

    partner_id = await loop.run_in_executor(
        None,
        lambda: client._get_or_create_partner(
            name=partner_name,
            email=partner_email,
            phone=partner_phone,
        ),
    )
    if partner_id in (False, None):
        partner_id = 1

    logger.info(
        "Odoo sync: conv_id=%s partner_id=%s content_preview=%r",
        conversation_id,
        partner_id,
        _log_preview(content, 160),
    )

    # Prefer inbox name from webhook; else resolve from Chatwoot API
    if not inbox_name and inbox_id:
        inbox_name = await get_inbox_name(inbox_id)
    contact_label = partner_name or partner_phone or "Contact"
    if inbox_name:
        channel_name = f"[{inbox_name}] Chat #{conversation_id} - {contact_label}"
    else:
        channel_name = f"Chat #{conversation_id} - {contact_label}"

    mapping = get_channel_for_conversation(conversation_id)

    if mapping:
        channel_id = mapping["channel_id"]
        mapped_pid = mapping.get("partner_id")
        logger.info(
            "Odoo channel reuse: conv_id=%s channel_id=%s mapped_partner_id=%s",
            conversation_id,
            channel_id,
            mapped_pid,
        )
        # Keep contact partner in sync (avoids poll mis-classifying author when partner was recreated)
        if mapped_pid != partner_id:
            set_channel_for_conversation(
                conversation_id,
                channel_id,
                partner_id,
                inbox_id=inbox_id if inbox_id is not None else mapping.get("inbox_id"),
            )
        # Backfill inbox_id in mapping if we have it (e.g. first message had no inbox, now it does)
        elif inbox_id is not None and mapping.get("inbox_id") is None:
            set_channel_for_conversation(
                conversation_id, channel_id, mapping["partner_id"], inbox_id=inbox_id
            )
    else:
        result = await loop.run_in_executor(
            None,
            lambda: client.create_or_get_discuss_channel(
                partner_id=partner_id,
                channel_name=channel_name,
                receevi_conversation_id=conversation_id,
                inbox_name=inbox_name,
            ),
        )
        if result.get("error"):
            return {"error": result["error"], "channel_id": None}
        channel_id = result["channel_id"]
        logger.info(
            "Odoo channel create/find: conv_id=%s channel_id=%s created=%s",
            conversation_id,
            channel_id,
            result.get("created"),
        )
        # Always set mapping so poll can send agent replies to Chatwoot/WhatsApp.
        set_channel_for_conversation(
            conversation_id, channel_id, partner_id, inbox_id=inbox_id
        )

    msg_result = await loop.run_in_executor(
        None,
        lambda: client.add_message_to_discuss(
            channel_id=channel_id,
            body=content,
            author_partner_id=partner_id,
        ),
    )
    if msg_result.get("error"):
        err = str(msg_result["error"])
        # Channel was deleted in Odoo; clear mapping and create a new channel
        if "Record does not exist or has been deleted" in err and mapping:
            logger.warning(
                "Odoo channel %s no longer exists (conversation_id=%s), clearing mapping and creating new channel",
                channel_id,
                conversation_id,
            )
            remove_channel_for_conversation(conversation_id)
            result = await loop.run_in_executor(
                None,
                lambda: client.create_or_get_discuss_channel(
                    partner_id=partner_id,
                    channel_name=channel_name,
                    receevi_conversation_id=conversation_id,
                    inbox_name=inbox_name,
                ),
            )
            if result.get("error"):
                return {"error": result["error"], "channel_id": None}
            channel_id = result["channel_id"]
            set_channel_for_conversation(
                conversation_id, channel_id, partner_id, inbox_id=inbox_id
            )
            msg_result = await loop.run_in_executor(
                None,
                lambda: client.add_message_to_discuss(
                    channel_id=channel_id,
                    body=content,
                    author_partner_id=partner_id,
                ),
            )
    if msg_result.get("error"):
        logger.error(
            "Odoo message_post FAILED: conv_id=%s channel_id=%s error=%s",
            conversation_id,
            channel_id,
            msg_result["error"],
        )
        return {"error": msg_result["error"], "channel_id": channel_id}

    mid = msg_result.get("message_id")
    logger.info(
        "Odoo message_post OK: conv_id=%s channel_id=%s odoo_mail_message_id=%s",
        conversation_id,
        channel_id,
        mid,
    )
    # So Odoo→Chatwoot poll does not treat this contact message as an "agent" reply to push back
    if mid:
        bump_last_message_id_if_newer(conversation_id, int(mid))
    return {"channel_id": channel_id, "message_id": mid}


async def add_chatwoot_agent_message_to_discuss(
    conversation_id: int,
    content: str,
) -> Dict[str, Any]:
    """
    Add a Chatwoot agent reply to Odoo Discuss.
    Uses Odoo user (ODOO_USERNAME) as the message author.
    """
    mapping = get_channel_for_conversation(conversation_id)
    if not mapping:
        logger.warning(
            "Odoo agent sync: no mapping for conv_id=%s (contact message never synced?)",
            conversation_id,
        )
        return {"error": f"No Odoo channel for conversation {conversation_id}", "channel_id": None}

    channel_id = mapping["channel_id"]
    client = get_odoo_client()
    loop = asyncio.get_event_loop()

    agent_partner_id = await loop.run_in_executor(
        None,
        lambda: client.get_odoo_user_partner_id(),
    )
    if not agent_partner_id:
        return {"error": "Could not get Odoo user partner", "channel_id": channel_id}

    msg_result = await loop.run_in_executor(
        None,
        lambda: client.add_message_to_discuss(
            channel_id=channel_id,
            body=content,
            author_partner_id=agent_partner_id,
        ),
    )
    if msg_result.get("error"):
        logger.error(
            "Odoo agent message_post FAILED: conv_id=%s channel_id=%s error=%s",
            conversation_id,
            channel_id,
            msg_result["error"],
        )
        return {"error": msg_result["error"], "channel_id": channel_id}

    # Prevent poll from re-sending this message to Chatwoot
    msg_id = msg_result.get("message_id")
    if msg_id:
        set_last_message_id(conversation_id, msg_id)

    logger.info(
        "Odoo agent message_post OK: conv_id=%s channel_id=%s odoo_mail_message_id=%s agent_partner_id=%s",
        conversation_id,
        channel_id,
        msg_id,
        agent_partner_id,
    )
    return {"channel_id": channel_id, "message_id": msg_id}


async def _poll_odoo_to_receevi() -> None:
    """
    Background task: poll Odoo Discuss for new agent messages,
    send them to Receevi (Chatwoot) as outgoing.
    """
    while True:
        try:
            await asyncio.sleep(DISCUSS_POLL_INTERVAL)
            if not RECEEVI_API_KEY:
                continue

            client = get_odoo_client()
            loop = asyncio.get_event_loop()
            mappings = get_all_conversation_mappings()
            if mappings:
                logger.debug(
                    "Odoo→Receevi poll: %d conversation(s) in mapping",
                    len(mappings),
                )

            agent_partner_id = await loop.run_in_executor(
                None,
                lambda: client.get_odoo_user_partner_id(),
            )
            if not agent_partner_id:
                continue

            for conv_id, data in mappings.items():
                channel_id = data.get("channel_id")
                partner_id = data.get("partner_id")
                if not channel_id or not partner_id:
                    continue

                last_id = get_last_message_id(conv_id)
                messages = await loop.run_in_executor(
                    None,
                    lambda cid=channel_id, lid=last_id, aid=agent_partner_id: client.get_discuss_messages_since(
                        channel_id=cid,
                        min_id=lid,
                        author_partner_ids_must_be=[aid],
                    ),
                )

                if messages:
                    logger.info(
                        "Odoo→Receevi poll: conv_id=%s channel_id=%s last_odoo_msg_id=%s new_agent_rows=%d",
                        conv_id,
                        channel_id,
                        last_id,
                        len(messages),
                    )
                for msg in messages:
                    plain = strip_html(msg.get("body", ""))
                    if not plain.strip():
                        logger.debug(
                            "Odoo→Receevi skip empty body: odoo_mail_message_id=%s conv_id=%s",
                            msg.get("id"),
                            conv_id,
                        )
                        continue
                    logger.info(
                        "Odoo→Receevi send: conv_id=%s odoo_mail_message_id=%s author_partner_id=%s preview=%r",
                        conv_id,
                        msg.get("id"),
                        msg.get("author_id"),
                        _log_preview(plain, 160),
                    )
                    err = await send_receevi_message(
                        conversation_id=conv_id,
                        content=plain,
                        message_type="outgoing",
                    )
                    if err.get("error"):
                        logger.warning(
                            "Odoo→Chatwoot send failed conv_id=%s: %s",
                            conv_id,
                            err.get("error"),
                        )
                    else:
                        set_last_message_id(conv_id, msg["id"])
                        _record_sent_to_chatwoot(conv_id, plain)
                        logger.info(
                            "Odoo→Receevi done: conv_id=%s odoo_mail_message_id=%s last_synced_id=%s",
                            conv_id,
                            msg["id"],
                            msg["id"],
                        )

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Odoo→Chatwoot poll loop error")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
