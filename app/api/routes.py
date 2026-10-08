"""JSON API: the integration surface a helpdesk (e.g. a Zendesk webhook) would call."""

import difflib
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.models import FeedbackIn, TicketIn
from app.observability import compute_metrics, get_logger

router = APIRouter()
log = get_logger(__name__)


@router.get("/healthz")
def healthz(request: Request) -> dict[str, Any]:
    return {"status": "ok", "llm_mode": request.app.state.settings.llm_mode, "db": request.app.state.db.ping()}


@router.post("/api/tickets", status_code=status.HTTP_202_ACCEPTED)
def create_ticket(ticket: TicketIn, request: Request, background: BackgroundTasks) -> dict[str, Any]:
    ticket_id = submit_ticket(request, background, ticket)
    return {"id": ticket_id, "status": "received"}


@router.get("/api/tickets")
def list_tickets(request: Request, status: str | None = None) -> list[dict[str, Any]]:
    return request.app.state.db.list_tickets(status=status)


@router.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: int, request: Request) -> dict[str, Any]:
    ticket = request.app.state.db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    ticket["llm_calls"] = request.app.state.db.list_llm_calls(ticket_id)
    return ticket


@router.post("/api/tickets/{ticket_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
def reprocess_ticket(ticket_id: int, request: Request, background: BackgroundTasks) -> dict[str, Any]:
    if request.app.state.db.get_ticket(ticket_id) is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    request.app.state.db.update_ticket(ticket_id, status="received")
    background.add_task(request.app.state.pipeline.process, ticket_id)
    return {"id": ticket_id, "status": "received"}


@router.post("/api/tickets/{ticket_id}/feedback")
def post_feedback(ticket_id: int, feedback: FeedbackIn, request: Request) -> dict[str, Any]:
    record_feedback(request, ticket_id, feedback)
    return {"id": ticket_id, "status": "done"}


@router.get("/api/metrics")
def metrics(request: Request) -> dict[str, Any]:
    return compute_metrics(request.app.state.db)


# --- shared with the web UI -----------------------------------------------------------------------

def submit_ticket(request: Request, background: BackgroundTasks, ticket: TicketIn) -> int:
    ticket_id = request.app.state.db.create_ticket(ticket.subject, ticket.body, ticket.customer_email)
    log.info("ticket_received", ticket_id=ticket_id)
    # Triage runs after the response is sent, so intake never waits on the model.
    background.add_task(request.app.state.pipeline.process, ticket_id)
    return ticket_id


def record_feedback(request: Request, ticket_id: int, feedback: FeedbackIn) -> None:
    db = request.app.state.db
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    if ticket["status"] in ("received", "processing"):
        raise HTTPException(status_code=409, detail="ticket is still being processed")
    if ticket["status"] == "done":
        # One decision per ticket: a second submission would double-count acceptance metrics.
        raise HTTPException(status_code=409, detail="feedback already recorded")

    triage = ticket["triage"] or {}
    draft_reply = (ticket["draft"] or {}).get("reply")
    action = feedback.action
    edit_ratio = None
    if draft_reply and feedback.final_reply is not None and action != "rejected":
        similarity = difflib.SequenceMatcher(None, draft_reply.strip(), feedback.final_reply.strip()).ratio()
        edit_ratio = round(1 - similarity, 3)
        if action == "accepted" and edit_ratio > 0:
            action = "edited"

    db.record_feedback({
        "ticket_id": ticket_id,
        "action": action,
        "final_reply": feedback.final_reply,
        "ai_category": triage.get("category"),
        "ai_priority": triage.get("priority"),
        "corrected_category": feedback.corrected_category if feedback.corrected_category != triage.get("category") else None,
        "corrected_priority": feedback.corrected_priority if feedback.corrected_priority != triage.get("priority") else None,
        "edit_ratio": edit_ratio,
    })
    db.update_ticket(ticket_id, status="done")
    log.info("agent_feedback", ticket_id=ticket_id, action=action, edit_ratio=edit_ratio)
