"""
Odoo XML-RPC client for Discuss (mail.channel) and partners.
Runs sync calls in a thread to work with FastAPI async endpoints.
"""
import html
import http.client
import logging
import re
import threading
import xmlrpc.client
from typing import Dict, Any, Optional, List
import os

logger = logging.getLogger(__name__)

ODOO_URL = os.getenv("ODOO_URL", "http://localhost:8069")
ODOO_DB = os.getenv("ODOO_DB", "odoo")
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "admin")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "admin")


def _html_escape(text: str) -> str:
    """Escape plain text for safe HTML body in Odoo."""
    return html.escape(text or "").replace("\n", "<br/>")


def strip_html(html_body: str) -> str:
    """Extract plain text from Odoo HTML body."""
    if not html_body:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", html_body or "")
    # Decode common entities
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return " ".join(text.split())


class OdooClient:
    """Sync Odoo client (use via asyncio.to_thread in async code)."""

    def __init__(
        self,
        url: Optional[str] = None,
        db: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.url = (url or ODOO_URL).rstrip("/")
        self.db = db or ODOO_DB
        self.username = username or ODOO_USERNAME
        self.password = password or ODOO_PASSWORD
        self.uid: Optional[int] = None
        self.models: Optional[Any] = None
        # xmlrpc.client reutiliza uma ligação HTTP; pedidos em paralelo (vários webhooks)
        # provocam CannotSendRequest / ResponseNotReady. RLock: authenticate dentro de _execute_kw.
        self._rpc_lock = threading.RLock()

    def _reset_session(self) -> None:
        """Drop cached XML-RPC session/proxy so next call can re-authenticate."""
        self.uid = None
        self.models = None

    @staticmethod
    def _is_transport_error(exc: Exception) -> bool:
        """Return True when error indicates broken/stale HTTP/XML-RPC connection."""
        if isinstance(exc, (OSError, ConnectionError, xmlrpc.client.ProtocolError, xmlrpc.client.ResponseError)):
            return True
        if isinstance(exc, (http.client.CannotSendRequest, http.client.ResponseNotReady)):
            return True
        msg = str(exc).lower()
        return (
            "connection refused" in msg
            or "request-sent" in msg
            or "cannotsendrequest" in msg
            or "connection reset" in msg
            or "timed out" in msg
            or "response not ready" in msg
            or msg == "idle"
        )

    def _execute_kw(self, model: str, method: str, args: List[Any], kwargs: Optional[Dict[str, Any]] = None) -> Any:
        """
        Execute Odoo XML-RPC method with one auto-reconnect retry for transport errors.
        """
        with self._rpc_lock:
            if not self.uid and not self.authenticate():
                raise RuntimeError("Odoo authentication failed")

            try:
                return self.models.execute_kw(
                    self.db, self.uid, self.password, model, method, args, kwargs or {}
                )
            except Exception as e:
                if not self._is_transport_error(e):
                    raise
                logger.warning("XML-RPC transport error; retrying once: %s", e)
                self._reset_session()
                if not self.authenticate():
                    raise
                return self.models.execute_kw(
                    self.db, self.uid, self.password, model, method, args, kwargs or {}
                )

    def get_odoo_user_partner_id(self) -> Optional[int]:
        """
        Get the partner_id of the Odoo user (ODOO_USERNAME).
        Used as author for agent messages synced from Chatwoot.
        """
        if not self.uid and not self.authenticate():
            return None
        try:
            users = self._execute_kw("res.users", "read", [self.uid], {"fields": ["partner_id"]})
            if users and users[0].get("partner_id"):
                pid = users[0]["partner_id"]
                return pid[0] if isinstance(pid, (list, tuple)) else pid
            return None
        except Exception as e:
            logger.warning("get_odoo_user_partner_id failed: %s", e)
            return None

    def authenticate(self) -> Optional[int]:
        """Authenticate with Odoo. Returns uid or None."""
        with self._rpc_lock:
            try:
                common = xmlrpc.client.ServerProxy(
                    f"{self.url}/xmlrpc/2/common", allow_none=True
                )
                self.uid = common.authenticate(
                    self.db, self.username, self.password, {}
                )
                if self.uid:
                    self.models = xmlrpc.client.ServerProxy(
                        f"{self.url}/xmlrpc/2/object", allow_none=True
                    )
                return self.uid
            except Exception as e:
                self.uid = None
                self.models = None
                logger.warning(
                    "Odoo auth failed: url=%s db=%s user=%s err=%s",
                    self.url, self.db, self.username, e,
                )
                return None

    def create_helpdesk_ticket(self, ticket_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a helpdesk ticket. Uses helpdesk.ticket if available,
        otherwise project.task as fallback.
        """
        if not self.uid and not self.authenticate():
            return {"error": "Odoo authentication failed", "id": None}

        partner_id = self._get_or_create_partner(
            name=ticket_data.get("partner_name"),
            email=ticket_data.get("partner_email"),
            phone=ticket_data.get("partner_phone"),
        )
        if partner_id is False or partner_id is None:
            partner_id = 1  # Fallback to first partner (e.g. Public User)

        # Try helpdesk.ticket first (Odoo Helpdesk module)
        try:
            ticket_id = self._execute_kw(
                "helpdesk.ticket",
                "create",
                [[
                    {
                        "name": ticket_data.get("name", "Incoming ticket"),
                        "description": ticket_data.get("description") or "",
                        "partner_id": partner_id,
                        "team_id": 1,
                    }
                ]],
            )
            return {"id": ticket_id, "model": "helpdesk.ticket", "status": "created"}
        except Exception:
            pass

        # Fallback: project.task (Project module)
        try:
            ticket_id = self._execute_kw(
                "project.task",
                "create",
                [[
                    {
                        "name": ticket_data.get("name", "Incoming ticket"),
                        "description": ticket_data.get("description") or "",
                        "partner_id": partner_id,
                    }
                ]],
            )
            return {"id": ticket_id, "model": "project.task", "status": "created"}
        except Exception as e:
            return {"error": str(e), "id": None}

    def create_or_get_discuss_channel(
        self,
        partner_id: int,
        channel_name: str,
        receevi_conversation_id: Optional[int] = None,
        inbox_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create or find a Discuss chat channel for a partner.
        For chat type: 1:1 between admin and contact.
        inbox_name: set on channel (chatwoot_inbox_name) so Odoo can list chats by SMS/WhatsApp/Website.
        Returns {"channel_id": int, "created": bool} or {"error": str}.
        """
        if not self.uid and not self.authenticate():
            return {"error": "Odoo authentication failed", "channel_id": None}

        # Search for existing chat channel with this partner
        try:
            channel_ids = self._execute_kw(
                "mail.channel",
                "search",
                [[
                    ("channel_type", "=", "chat"),
                    ("channel_partner_ids", "in", [partner_id]),
                ]],
                {"limit": 1},
            )
            if channel_ids:
                channel_id = channel_ids[0]
                # Backfill chatwoot_inbox_name if we have it and the channel doesn't
                if inbox_name:
                    try:
                        channels = self._execute_kw(
                            "mail.channel", "read", [channel_id], {"fields": ["chatwoot_inbox_name"]}
                        )
                        if channels and not channels[0].get("chatwoot_inbox_name"):
                            self._execute_kw(
                                "mail.channel", "write", [channel_id], {"chatwoot_inbox_name": inbox_name}
                            )
                    except Exception:
                        pass
                return {"channel_id": channel_id, "created": False}
        except Exception:
            pass

        # Create new chat channel (chatwoot_inbox_name requires linhafala_chatwoot module)
        vals = {
            "name": channel_name or "Chat",
            "channel_type": "chat",
            "channel_partner_ids": [(4, partner_id, 0)],
        }
        if inbox_name:
            vals["chatwoot_inbox_name"] = inbox_name
        try:
            channel_id = self._execute_kw("mail.channel", "create", [vals])
            return {"channel_id": channel_id, "created": True}
        except Exception as e:
            if inbox_name and "chatwoot_inbox_name" in str(e).lower():
                vals.pop("chatwoot_inbox_name", None)
                try:
                    channel_id = self._execute_kw("mail.channel", "create", [vals])
                    return {"channel_id": channel_id, "created": True}
                except Exception as e2:
                    logger.exception("create_or_get_discuss_channel failed: %s", e2)
                    return {"error": str(e2), "channel_id": None}
            logger.exception("create_or_get_discuss_channel failed: %s", e)
            return {"error": str(e), "channel_id": None}

    def add_message_to_discuss(
        self,
        channel_id: int,
        body: str,
        author_partner_id: int,
    ) -> Dict[str, Any]:
        """
        Add a message to a Discuss channel. Author is the partner (contact).
        Uses message_post on mail.channel (more reliable than direct mail.message create).
        Returns {"message_id": int} or {"error": str}.
        """
        if not self.uid and not self.authenticate():
            return {"error": "Odoo authentication failed", "message_id": None}

        body_html = f"<p>{_html_escape(body)}</p>" if body else "<p></p>"

        try:
            # Use message_post on the channel - supports author_id for external sender
            msg_id = self._execute_kw(
                "mail.channel",
                "message_post",
                [channel_id],
                {
                    "body": body_html,
                    "message_type": "comment",
                    "subtype_xmlid": "mail.mt_comment",
                    "author_id": author_partner_id,
                },
            )
            return {"message_id": msg_id}
        except Exception as e:
            logger.exception("add_message_to_discuss failed: %s", e)
            return {"error": str(e), "message_id": None}

    def get_discuss_messages_since(
        self,
        channel_id: int,
        min_id: int,
        exclude_author_ids: Optional[List[int]] = None,
        author_partner_ids_must_be: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get messages in a channel with id > min_id.
        exclude_author_ids: skip messages from these partners (legacy).
        author_partner_ids_must_be: if set, only return messages authored by these partners
        (use integration user's partner so contact/webhook posts are never pushed back to Chatwoot).
        """
        if not self.uid and not self.authenticate():
            return []

        # Odoo 16.0 usa mail.channel; builds mais recentes podem usar discuss.channel
        domain = [
            ("model", "in", ["mail.channel", "discuss.channel"]),
            ("res_id", "=", channel_id),
            ("id", ">", min_id),
            ("message_type", "=", "comment"),
        ]
        if author_partner_ids_must_be:
            domain.append(("author_id", "in", author_partner_ids_must_be))
        elif exclude_author_ids:
            domain.append(("author_id", "not in", exclude_author_ids))

        try:
            msg_ids = self._execute_kw("mail.message", "search", [domain], {"order": "id asc"})
            if not msg_ids:
                return []

            msg_records = self._execute_kw(
                "mail.message", "read", [msg_ids], {"fields": ["id", "body", "author_id"]}
            )
            result = []
            for m in msg_records:
                author_id = m.get("author_id")
                if isinstance(author_id, (list, tuple)) and len(author_id) >= 1:
                    author_id = author_id[0]
                result.append({
                    "id": m["id"],
                    "body": m.get("body") or "",
                    "author_id": author_id,
                })
            return result
        except Exception:
            logger.exception(
                "get_discuss_messages_since failed channel_id=%s min_id=%s",
                channel_id,
                min_id,
            )
            return []

    def _get_or_create_partner(
        self,
        name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> int:
        """Find existing partner by email/phone or create new one. Returns partner id."""
        if not self.uid:
            self.authenticate()

        search_domain = []
        if email:
            search_domain = [("email", "=", email)]
        elif phone:
            search_domain = ["|", ("phone", "=", phone), ("mobile", "=", phone)]

        if search_domain:
            try:
                partner_ids = self._execute_kw("res.partner", "search", [search_domain], {"limit": 1})
                if partner_ids:
                    return partner_ids[0]
            except Exception:
                pass

        # Create new partner
        vals = {"name": name or "Unknown"}
        if email:
            vals["email"] = email
        if phone:
            vals["phone"] = phone
        try:
            partner_id = self._execute_kw("res.partner", "create", [vals])
            return partner_id
        except Exception:
            return False  # type: ignore[return-value]


# Singleton for reuse
_odoo_client: Optional[OdooClient] = None


def get_odoo_client() -> OdooClient:
    global _odoo_client
    if _odoo_client is None:
        _odoo_client = OdooClient()
    return _odoo_client
