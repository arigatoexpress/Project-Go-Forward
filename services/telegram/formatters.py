"""Deal-alert formatters for Telegram/Mira messages.

Telegram Bot API supports three parse modes for message text:

* ``None`` / ``"plain"`` — raw text, no formatting.
* ``"HTML"`` — a small subset of HTML tags (``<b>``, ``<i>``, ``<a>``, etc.).
* ``"MarkdownV2"`` — Telegram's flavour of Markdown with strict escaping.

All formatters are pure functions: they take a ``database.models.Deal``,
a plain Firestore dict, or any object with the expected attributes and return
formatted text. No outbound I/O.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DealLike(Protocol):
    """Minimal shape of a deal object that the formatters consume.

    The formatters only read a handful of fields, so anything that exposes these
    attributes works — a ``database.models.Deal`` instance, a plain dict, or a
    dictionary wrapped via ``types.SimpleNamespace``.
    """

    id: str
    status: str | None
    buyer_first_name: str | None
    buyer_last_name: str | None
    buyer_phone: str | None
    buyer_email: str | None
    salesrep: str | None
    manufacturer: str | None
    model: str | None
    serial_number_1: str | None
    year: str | None
    sales_price: float | Decimal | None
    down_payment: float | Decimal | None
    created_at: datetime | str | None


# Telegram MarkdownV2 reserved characters that must be escaped in entity text.
# See: https://core.telegram.org/bots/api#markdownv2-style
_MARKDOWNV2_RESERVED = frozenset("_*[]()~`>#+-=|{}.!")


def escape_markdownv2(text: str | None) -> str:
    """Escape characters that have special meaning in Telegram MarkdownV2.

    Parameters
    ----------
    text : str | None
        Raw text to escape. ``None`` is treated as an empty string.

    Returns
    -------
    str
        Text with each reserved character prefixed by a backslash.
    """
    if not text:
        return ""
    return "".join(f"\\{ch}" if ch in _MARKDOWNV2_RESERVED else ch for ch in str(text))


def escape_html(text: str | None) -> str:
    """Escape characters that have special meaning in HTML.

    Parameters
    ----------
    text : str | None
        Raw text to escape. ``None`` is treated as an empty string.

    Returns
    -------
    str
        Text with ``&``, ``<``, and ``>`` replaced by their HTML entities.
    """
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _coerce_str(value: Any) -> str:
    """Return a string representation of ``value`` or an empty string.

    ``None`` yields ``""``. Booleans are rendered as ``"Yes"`` / ``"No"``.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _money(value: float | Decimal | None) -> str:
    """Format a numeric value as US currency, or return ``"N/A"``.

    Parameters
    ----------
    value : float | Decimal | None

    Returns
    -------
    str
        e.g. ``"$125,000.00"`` or ``"N/A"``.
    """
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (ValueError, TypeError):
        return str(value)


def _getattr(deal: DealLike | dict[str, Any], name: str, default: Any = None) -> Any:
    """Read an attribute from an object or a key from a dict."""
    if isinstance(deal, dict):
        return deal.get(name, default)
    return getattr(deal, name, default)


def _buyer_name(deal: DealLike | dict[str, Any]) -> str:
    """Return the buyer's full name or ``"Unknown buyer"``."""
    parts = [
        p
        for p in [
            _getattr(deal, "buyer_first_name"),
            _getattr(deal, "buyer_last_name"),
        ]
        if p
    ]
    return " ".join(parts) if parts else "Unknown buyer"


def _home_summary(deal: DealLike | dict[str, Any]) -> str:
    """Return a compact manufacturer/model/serial description."""
    parts: list[str] = []
    year = _getattr(deal, "year")
    manufacturer = _getattr(deal, "manufacturer")
    model = _getattr(deal, "model")
    serial = _getattr(deal, "serial_number_1")

    if year:
        parts.append(str(year))
    if manufacturer:
        parts.append(str(manufacturer))
    if model:
        parts.append(str(model))

    summary = " ".join(parts) if parts else "Home TBD"
    if serial:
        summary += f" (S/N: {serial})"
    return summary


def _unpaid_balance(deal: DealLike | dict[str, Any]) -> float | Decimal | None:
    """Compute ``sales_price - down_payment`` when both values are available."""
    sales_price = _getattr(deal, "sales_price")
    down_payment = _getattr(deal, "down_payment")
    if sales_price is None or down_payment is None:
        return None
    try:
        return max(0, float(sales_price) - float(down_payment))
    except (ValueError, TypeError):
        return None


def _timestamp(deal: DealLike | dict[str, Any]) -> str:
    """Return an ISO-ish timestamp for the deal's ``created_at`` value."""
    created_at = _getattr(deal, "created_at")
    if created_at is None:
        return "N/A"
    if isinstance(created_at, datetime):
        return created_at.strftime("%Y-%m-%d %H:%M UTC")
    return str(created_at)


def _status_emoji(status: str | None) -> str:
    """Return a small emoji prefix that mirrors common THO deal statuses."""
    status_value = (status or "").lower()
    mapping = {
        "pending": "⏳",
        "approved": "✅",
        "contract": "📝",
        "funded": "💰",
        "complete": "🎉",
        "denied": "❌",
        "archived": "🗄️",
    }
    return mapping.get(status_value, "📋")


# ═══════════════════════════════════════════════════════════════════════════════
# Full deal-alert formatters
# ═══════════════════════════════════════════════════════════════════════════════


def format_deal_alert_plain(deal: DealLike | dict[str, Any], *, include_emoji: bool = True) -> str:
    """Return a plain-text deal alert suitable for Telegram ``text`` field.

    Parameters
    ----------
    deal : DealLike | dict[str, Any]
        Deal or deal-shaped object to render.
    include_emoji : bool, optional
        When ``True`` (default), prefix the header with a status emoji.

    Returns
    -------
    str
        Plain-text alert with line breaks.
    """
    status = _coerce_str(_getattr(deal, "status")) or "unknown"
    emoji = _status_emoji(status) if include_emoji else ""
    header = f"{emoji} DEAL ALERT — status: {status.upper()}".strip()

    lines = [
        header,
        "",
        f"Deal ID:    {_getattr(deal, 'id', 'N/A')}",
        f"Buyer:      {_buyer_name(deal)}",
        f"Phone:      {_coerce_str(_getattr(deal, 'buyer_phone')) or 'N/A'}",
        f"Email:      {_coerce_str(_getattr(deal, 'buyer_email')) or 'N/A'}",
        f"Home:       {_home_summary(deal)}",
        f"Sales rep:  {_coerce_str(_getattr(deal, 'salesrep')) or 'Unassigned'}",
        "",
        f"Sales price:    {_money(_getattr(deal, 'sales_price'))}",
        f"Down payment:   {_money(_getattr(deal, 'down_payment'))}",
        f"Unpaid balance: {_money(_unpaid_balance(deal))}",
        "",
        f"Created: {_timestamp(deal)}",
    ]
    return "\n".join(lines)


def format_deal_alert_html(deal: DealLike | dict[str, Any], *, include_emoji: bool = True) -> str:
    """Return an HTML deal alert for Telegram ``parse_mode='HTML'``.

    All dynamic values are HTML-escaped before rendering.

    Parameters
    ----------
    deal : DealLike | dict[str, Any]
        Deal or deal-shaped object to render.
    include_emoji : bool, optional
        When ``True`` (default), prefix the header with a status emoji.

    Returns
    -------
    str
        HTML alert with ``<b>`` labels and escaped values.
    """
    status = _coerce_str(_getattr(deal, "status")) or "unknown"
    emoji = _status_emoji(status) if include_emoji else ""
    header = (f"{emoji} <b>DEAL ALERT</b> — status: <b>{escape_html(status.upper())}</b>").strip()

    lines = [
        header,
        "",
        f"<b>Deal ID:</b> {escape_html(_getattr(deal, 'id', 'N/A'))}",
        f"<b>Buyer:</b> {escape_html(_buyer_name(deal))}",
        f"<b>Phone:</b> {escape_html(_coerce_str(_getattr(deal, 'buyer_phone')) or 'N/A')}",
        f"<b>Email:</b> {escape_html(_coerce_str(_getattr(deal, 'buyer_email')) or 'N/A')}",
        f"<b>Home:</b> {escape_html(_home_summary(deal))}",
        f"<b>Sales rep:</b> {escape_html(_coerce_str(_getattr(deal, 'salesrep')) or 'Unassigned')}",
        "",
        f"<b>Sales price:</b> {escape_html(_money(_getattr(deal, 'sales_price')))}",
        f"<b>Down payment:</b> {escape_html(_money(_getattr(deal, 'down_payment')))}",
        f"<b>Unpaid balance:</b> {escape_html(_money(_unpaid_balance(deal)))}",
        "",
        f"<b>Created:</b> {escape_html(_timestamp(deal))}",
    ]
    return "\n".join(lines)


def format_deal_alert_markdownv2(
    deal: DealLike | dict[str, Any], *, include_emoji: bool = True
) -> str:
    """Return a MarkdownV2 deal alert for Telegram ``parse_mode='MarkdownV2'``.

    All dynamic values are escaped to prevent parse errors from characters such
    as ``.``, ``-``, ``(``, ``)``, or user-supplied punctuation.

    Parameters
    ----------
    deal : DealLike | dict[str, Any]
        Deal or deal-shaped object to render.
    include_emoji : bool, optional
        When ``True`` (default), prefix the header with a status emoji.

    Returns
    -------
    str
        MarkdownV2 alert with bold labels and escaped values.
    """
    status = _coerce_str(_getattr(deal, "status")) or "unknown"
    emoji = _status_emoji(status) if include_emoji else ""
    header = (f"{emoji} *DEAL ALERT* — status: *{escape_markdownv2(status.upper())}*").strip()

    lines = [
        header,
        "",
        f"*Deal ID:* `{escape_markdownv2(_getattr(deal, 'id', 'N/A'))}`",
        f"*Buyer:* {escape_markdownv2(_buyer_name(deal))}",
        f"*Phone:* {escape_markdownv2(_coerce_str(_getattr(deal, 'buyer_phone')) or 'N/A')}",
        f"*Email:* {escape_markdownv2(_coerce_str(_getattr(deal, 'buyer_email')) or 'N/A')}",
        f"*Home:* {escape_markdownv2(_home_summary(deal))}",
        f"*Sales rep:* {escape_markdownv2(_coerce_str(_getattr(deal, 'salesrep')) or 'Unassigned')}",
        "",
        f"*Sales price:* {escape_markdownv2(_money(_getattr(deal, 'sales_price')))}",
        f"*Down payment:* {escape_markdownv2(_money(_getattr(deal, 'down_payment')))}",
        f"*Unpaid balance:* {escape_markdownv2(_money(_unpaid_balance(deal)))}",
        "",
        f"*Created:* {escape_markdownv2(_timestamp(deal))}",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Compact one-line / short summary formatters
# ═══════════════════════════════════════════════════════════════════════════════


def format_deal_summary_plain(deal: DealLike | dict[str, Any]) -> str:
    """Return a compact one-line plain-text summary.

    Useful for notification previews, callback button text, or log lines.
    """
    deal_id = _coerce_str(_getattr(deal, "id", "N/A"))
    return (
        f"[{deal_id[:8]}] {_buyer_name(deal)} — "
        f"{_home_summary(deal)} — {_money(_getattr(deal, 'sales_price'))}"
    )


def format_deal_summary_html(deal: DealLike | dict[str, Any]) -> str:
    """Return a compact one-line HTML summary."""
    return escape_html(format_deal_summary_plain(deal))


def format_deal_summary_markdownv2(deal: DealLike | dict[str, Any]) -> str:
    """Return a compact one-line MarkdownV2 summary.

    Useful for inline keyboard button text where formatting is allowed.
    """
    return escape_markdownv2(format_deal_summary_plain(deal))


def escape_markdown_legacy(text: str | None) -> str:
    """Escape characters reserved by Telegram's legacy ``Markdown`` parse mode.

    This matches the escaping used elsewhere in the Telegram module for
    ``parse_mode='Markdown'`` and keeps ``format_deal_summary`` compatible with
    callers that have not yet migrated to MarkdownV2.
    """
    if not text:
        return ""
    reserved = frozenset("_*[]()~`")
    return "".join(f"\\{ch}" if ch in reserved else ch for ch in str(text))


def format_deal_summary(deal: DealLike | dict[str, Any]) -> str:
    """Backward-compatible compact Markdown summary used by other modules.

    This matches the original signature and returns text safe for Telegram's
    legacy ``parse_mode='Markdown'`` with the ``*New deal awaiting approval*``
    header. For MarkdownV2 output, use :func:`format_deal_summary_markdownv2`.
    """
    status = _coerce_str(_getattr(deal, "status")) or "pending"
    emoji = _status_emoji(status)
    return (
        f"{emoji} *New deal awaiting approval*\n"
        f"\n"
        f"*Buyer:* {escape_markdown_legacy(_buyer_name(deal))}\n"
        f"*Home:* {escape_markdown_legacy(_home_summary(deal))}\n"
        f"*Sales price:* {escape_markdown_legacy(_money(_getattr(deal, 'sales_price')))}\n"
        f"*Down payment:* {escape_markdown_legacy(_money(_getattr(deal, 'down_payment')))}\n"
        f"*Sales rep:* {escape_markdown_legacy(_coerce_str(_getattr(deal, 'salesrep')) or 'Unassigned')}\n"
        f"*Status:* `{escape_markdown_legacy(status)}`\n"
        f"*Deal ID:* `{escape_markdown_legacy(_coerce_str(_getattr(deal, 'id', 'unknown')))}`"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatcher
# ═══════════════════════════════════════════════════════════════════════════════


PARSE_MODES: dict[str, Any] = {
    "plain": format_deal_alert_plain,
    "text": format_deal_alert_plain,
    "html": format_deal_alert_html,
    "markdownv2": format_deal_alert_markdownv2,
    "markdown_v2": format_deal_alert_markdownv2,
}


def format_deal_alert(
    deal: DealLike | dict[str, Any],
    parse_mode: str | None = None,
    *,
    include_emoji: bool = True,
    from_status: str | None = None,
    to_status: str | None = None,
) -> str:
    """Dispatch to the appropriate formatter based on ``parse_mode``.

    Parameters
    ----------
    deal : DealLike | dict[str, Any]
        Deal or deal-shaped object to render.
    parse_mode : str | None
        One of ``"HTML"``, ``"MarkdownV2"``, ``"plain"``, or ``None``. Matching
        is case-insensitive; whitespace/underscores are normalised.
    include_emoji : bool, optional
        Forwarded to the underlying formatter.
    from_status : str | None, optional
        Previous deal status. When provided alongside ``to_status``, a status
        change line is appended to the alert.
    to_status : str | None, optional
        New deal status.

    Returns
    -------
    str
        Formatted alert text.
    """
    if parse_mode is None:
        text = format_deal_alert_plain(deal, include_emoji=include_emoji)
    else:
        normalised = str(parse_mode).lower().replace(" ", "").replace("_", "")
        formatter = PARSE_MODES.get(normalised, format_deal_alert_plain)
        text = formatter(deal, include_emoji=include_emoji)

    if from_status or to_status:
        normalised = (
            str(parse_mode).lower().replace(" ", "").replace("_", "") if parse_mode else "plain"
        )
        if normalised == "html":
            status_line = (
                f"\n<b>Status change:</b> "
                f"{escape_html(from_status or '—')} → {escape_html(to_status or '—')}"
            )
        elif normalised in ("markdownv2", "markdown_v2"):
            status_line = (
                f"\n*Status change:* "
                f"{escape_markdownv2(from_status or '—')} → {escape_markdownv2(to_status or '—')}"
            )
        else:
            status_line = f"\nStatus change: {from_status or '—'} → {to_status or '—'}"
        text += status_line

    return text


def format_webhook_event_alert(
    event: str,
    deal_id: str,
    payload: dict[str, Any],
    *,
    parse_mode: str = "HTML",
) -> str:
    """Format a partner webhook arrival alert for Telegram.

    Parameters
    ----------
    event : str
        Partner event name, e.g. ``"deal.status_changed"``.
    deal_id : str
        Deal identifier.
    payload : dict[str, Any]
        Event payload summary.
    parse_mode : str, optional
        Target Telegram parse mode. Defaults to ``"HTML"``.

    Returns
    -------
    str
        Formatted alert text.
    """
    normalised = str(parse_mode).lower().replace(" ", "").replace("_", "")
    summary = payload.get("status") or payload.get("to") or payload.get("event") or "N/A"

    if normalised == "html":
        lines = [
            "<b>🔔 Partner Webhook</b>",
            "",
            f"<b>Event:</b> <code>{escape_html(event)}</code>",
            f"<b>Deal:</b> <code>{escape_html(deal_id)}</code>",
            f"<b>Status:</b> {escape_html(str(summary))}",
        ]
    elif normalised in ("markdownv2", "markdown_v2"):
        lines = [
            "🔔 *Partner Webhook*",
            "",
            f"*Event:* `{escape_markdownv2(event)}`",
            f"*Deal:* `{escape_markdownv2(deal_id)}`",
            f"*Status:* {escape_markdownv2(str(summary))}",
        ]
    else:
        lines = [
            "🔔 Partner Webhook",
            "",
            f"Event: {event}",
            f"Deal: {deal_id}",
            f"Status: {summary}",
        ]
    return "\n".join(lines)


__all__ = [
    "DealLike",
    "escape_html",
    "escape_markdown_legacy",
    "escape_markdownv2",
    "format_deal_alert",
    "format_deal_alert_html",
    "format_deal_alert_markdownv2",
    "format_deal_alert_plain",
    "format_deal_summary",
    "format_deal_summary_html",
    "format_deal_summary_markdownv2",
    "format_deal_summary_plain",
    "format_webhook_event_alert",
]
