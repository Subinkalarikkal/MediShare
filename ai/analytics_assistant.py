"""Read-only administrative analytics assistant.

Architecture: question → permission check → fixed aggregated queries
(analytics_service.assistant_snapshot) → Claude → response validation.

Claude never receives SQL access, raw tables, or personal data, and has no
tools that could modify the database. Responses that look like approvals,
authorizations or clinical advice are blocked.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ai import claude_client
from services import analytics_service
from services.permission_service import Actor, require
from utils.audit_logger import audit
from utils.constants import Perm

SYSTEM_PROMPT = (
    "You are the MediShare operations analytics assistant for platform administrators. Answer ONLY from the "
    "JSON metrics provided in the user turn; if the metrics do not contain the answer, say so plainly. Be concise "
    "and use short bullet points or a small table. You are read-only: you cannot change data, approve or authorize "
    "anything, or make regulatory decisions. Never give clinical advice (uses, dosage, substitutions, suitability) "
    "and never state that any medicine or transfer is approved, authorized, safe or suitable."
)

BLOCK_PATTERNS = [
    r"\b(i|we)\s+(hereby\s+)?(approve|authori[sz]e|certify)\b",
    r"\bis\s+(now\s+)?(approved|authori[sz]ed)\s+for\s+(transfer|redistribution|reuse|donation)\b",
    r"\b(recommended|suggested)\s+(dose|dosage)\b",
    r"\b(take|administer)\s+\d+\s*(mg|ml|tablets?)\b",
    r"\bsafe\s+(to\s+use|for\s+(patients|reuse|consumption))\b",
    r"\bcan\s+be\s+(substituted|used\s+instead)\b",
]
MAX_QUESTION_LEN = 500


@dataclass
class AssistantAnswer:
    text: str
    source: str  # claude | fallback | blocked
    snapshot_keys: list[str]


def validate_response(text: str) -> tuple[bool, str]:
    for pat in BLOCK_PATTERNS:
        if re.search(pat, text, re.I):
            return False, ("The generated answer was withheld because it contained approval, authorization or "
                           "clinical language, which the assistant is not permitted to produce. Please rephrase.")
    return True, text


def _fallback(question: str, snap: dict) -> str:
    q = question.lower()
    k = snap["kpis"]
    lines = ["*AI narrative unavailable – showing the relevant database figures.*", ""]
    if "pending" in q or "request" in q:
        lines.append(f"- Pending collection requests: **{k['pending_collection_requests']}**")
        lines.append(f"- Completed collections: **{k['completed_collections']}**")
    if "district" in q:
        for row in snap["district_registrations"][:5]:
            lines.append(f"- {row['district']}: {row['registrations']} registrations")
    if "delay" in q or "centre" in q:
        lines.append(f"- Delayed requests: **{len(snap['delayed_requests'])}**")
        for d in snap["delayed_requests"][:10]:
            lines.append(f"  - {d['centre']} – {d['status']} since {d['requested']}")
    if "attention" in q or "expir" in q or "inventory" in q:
        lines.append(f"- Inventory entries expiring within 90 days: **{len(snap['inventory_needing_attention'])}**")
        for a in snap["inventory_needing_attention"][:10]:
            lines.append(f"  - {a['medicine']} ({a['batch']}) – {a['organization']}, {a['days_left']} days left")
    if "document" in q or "incomplete" in q:
        lines.append(f"- Records with documentation gaps: **{len(snap['incomplete_documentation'])}**")
        for d in snap["incomplete_documentation"][:10]:
            lines.append(f"  - {d['organization']}: {d['gaps']}")
    if "month" in q or "summar" in q or len(lines) == 2:
        m = snap["this_month"]
        lines += [f"- Registrations this month: **{m['registrations']}**",
                  f"- Collection requests this month: **{m['collection_requests']}**",
                  f"- Collections completed this month: **{m['collections_completed']}**",
                  f"- Disposals recorded this month: **{m['disposals']}**"]
    return "\n".join(lines)


def ask(session: Session, actor: Actor, question: str) -> AssistantAnswer:
    fresh = require(session, actor, Perm.AI_ASSISTANT, Perm.ANALYTICS_VIEW)
    question = (question or "").strip()[:MAX_QUESTION_LEN]
    if not question:
        from services.errors import ValidationError
        raise ValidationError("Please enter a question.")
    snap = analytics_service.assistant_snapshot(session, fresh)
    keys = sorted(snap.keys())
    audit(session, fresh.user_id, "AI_ASSISTANT_QUERY", "ai_assistant", None, details=question)
    if not claude_client.is_enabled():
        return AssistantAnswer(_fallback(question, snap), "fallback", keys)
    payload = json.dumps(snap, default=str)
    try:
        text = claude_client.create_message(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"<metrics>\n{payload}\n</metrics>\n\nQuestion: {question}"}],
            max_tokens=3000)
    except claude_client.ClaudeUnavailable as exc:
        return AssistantAnswer(f"{exc}\n\n" + _fallback(question, snap), "fallback", keys)
    ok, safe_text = validate_response(text)
    return AssistantAnswer(safe_text, "claude" if ok else "blocked", keys)
