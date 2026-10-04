"""Server-rendered agent UI (Jinja2 + HTMX). Thin layer over the same functions the JSON API uses."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.api.routes import record_feedback, submit_ticket
from app.models import CATEGORIES, PRIORITIES, FeedbackIn, TicketIn
from app.observability import compute_metrics

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

# Queue order: what needs a human first, then ready drafts, then everything else.
_STATUS_RANK = {"needs_review": 0, "ai_unavailable": 1, "ready": 2, "processing": 3, "received": 3, "done": 9}
_PRIORITY_RANK = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


def _queue(request: Request) -> list[dict[str, Any]]:
    tickets = request.app.state.db.list_tickets()

    def key(t: dict[str, Any]) -> tuple[int, int, int]:
        priority = (t["triage"] or {}).get("priority")
        return (_STATUS_RANK.get(t["status"], 5), _PRIORITY_RANK.get(priority, 4), -t["id"])

    return sorted(tickets, key=key)


@router.get("/", response_class=HTMLResponse)
def queue_page(request: Request):
    return templates.TemplateResponse(request, "queue.html", {"tickets": _queue(request)})


@router.get("/partials/queue", response_class=HTMLResponse)
def queue_partial(request: Request):
    return templates.TemplateResponse(request, "_queue_rows.html", {"tickets": _queue(request)})


@router.get("/tickets/new", response_class=HTMLResponse)
def new_ticket_page(request: Request):
    return templates.TemplateResponse(request, "new_ticket.html", {"errors": [], "form": {}})


@router.post("/tickets", response_class=HTMLResponse)
def create_ticket_form(
    request: Request,
    background: BackgroundTasks,
    subject: str = Form(""),
    body: str = Form(""),
    customer_email: str = Form(""),
):
    form = {"subject": subject, "body": body, "customer_email": customer_email}
    try:
        ticket = TicketIn(**form)
    except ValidationError as exc:
        errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return templates.TemplateResponse(
            request, "new_ticket.html", {"errors": errors, "form": form}, status_code=422
        )
    ticket_id = submit_ticket(request, background, ticket)
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


def _ticket_context(request: Request, ticket_id: int) -> dict[str, Any]:
    db = request.app.state.db
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    pipeline = request.app.state.pipeline
    articles = pipeline.kb.get(ticket["article_ids"] or [])
    calls = db.list_llm_calls(ticket_id)
    return {
        "t": ticket,
        "articles": articles,
        "grounding": pipeline.grounding,
        "calls": calls,
        "total_cost": sum(c["cost_usd"] or 0 for c in calls),
        "categories": CATEGORIES,
        "priorities": PRIORITIES,
    }


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_page(request: Request, ticket_id: int):
    return templates.TemplateResponse(request, "ticket.html", _ticket_context(request, ticket_id))


@router.get("/partials/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_partial(request: Request, ticket_id: int):
    return templates.TemplateResponse(request, "_ticket_body.html", _ticket_context(request, ticket_id))


@router.post("/tickets/{ticket_id}/feedback")
def ticket_feedback_form(
    request: Request,
    ticket_id: int,
    action: str = Form(...),
    final_reply: str = Form(""),
    corrected_category: str = Form(""),
    corrected_priority: str = Form(""),
):
    try:
        feedback = FeedbackIn(
            action=action,  # type: ignore[arg-type]
            final_reply=final_reply or None,
            corrected_category=corrected_category or None,  # type: ignore[arg-type]
            corrected_priority=corrected_priority or None,  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    record_feedback(request, ticket_id, feedback)
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@router.post("/tickets/{ticket_id}/reprocess")
def ticket_reprocess_form(request: Request, ticket_id: int, background: BackgroundTasks):
    request.app.state.db.update_ticket(ticket_id, status="received")
    background.add_task(request.app.state.pipeline.process, ticket_id)
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@router.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request):
    return templates.TemplateResponse(request, "metrics.html", {"m": compute_metrics(request.app.state.db)})
