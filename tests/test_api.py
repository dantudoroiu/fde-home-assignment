import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.llm.client import LLMError
from app.main import create_app
from tests.conftest import ScriptedClient, make_draft, make_triage

TICKET = {"subject": "Invoice question", "body": "Why is INV-20417 higher?", "customer_email": "a@example.com"}


@pytest.fixture
def client_factory(settings):
    def factory(llm):
        return TestClient(create_app(settings=settings, llm=llm))
    return factory


def test_ticket_lifecycle_create_triage_feedback(client_factory):
    llm = ScriptedClient(triage=make_triage(), draft=make_draft())
    with client_factory(llm) as client:
        resp = client.post("/api/tickets", json=TICKET)
        assert resp.status_code == 202
        tid = resp.json()["id"]

        # TestClient runs background tasks before returning, so triage has completed here.
        ticket = client.get(f"/api/tickets/{tid}").json()
        assert ticket["status"] == "ready"
        assert ticket["triage"]["category"] == "billing"

        edited = ticket["draft"]["reply"] + "\nP.S. Happy to walk you through it on a call."
        resp = client.post(
            f"/api/tickets/{tid}/feedback",
            json={"action": "accepted", "final_reply": edited, "corrected_priority": "P2"},
        )
        assert resp.status_code == 200

        metrics = client.get("/api/metrics").json()
        assert metrics["agent_feedback"]["actions"] == {"edited": 1}  # changed text => edited
        assert metrics["agent_feedback"]["priority_agreement"] == 0.0
        assert metrics["agent_feedback"]["category_agreement"] == 1.0
        assert client.get(f"/api/tickets/{tid}").json()["status"] == "done"


def test_startup_recovers_ticket_orphaned_by_a_crash(settings, client_factory):
    # A previous run saved the ticket and replied 202, then died before the pipeline ran.
    db = Database(settings.database_path)
    db.init_schema()
    tid = db.create_ticket("Invoice question", "Why is INV-20417 higher?", "a@example.com")

    llm = ScriptedClient(triage=make_triage(), draft=make_draft())
    with client_factory(llm) as client:
        # A ticket submitted right after startup must not be mistaken for an orphan.
        new_tid = client.post("/api/tickets", json=TICKET).json()["id"]
        client.app.state.recovery.join(timeout=5)
        assert client.get(f"/api/tickets/{tid}").json()["status"] == "ready"
        assert sum(c["ticket_id"] == new_tid and c["step"] == "triage" for c in llm.calls) == 1


def test_llm_outage_still_accepts_ticket(client_factory):
    llm = ScriptedClient(triage=LLMError("connection", "down"))
    with client_factory(llm) as client:
        resp = client.post("/api/tickets", json=TICKET)
        assert resp.status_code == 202
        ticket = client.get(f"/api/tickets/{resp.json()['id']}").json()
        assert ticket["status"] == "ai_unavailable"
        assert client.get("/api/metrics").json()["ai_unavailable_rate"] == 1.0


@pytest.mark.parametrize(
    "payload",
    [
        {**TICKET, "body": ""},
        {**TICKET, "body": "x" * 8001},
        {**TICKET, "subject": ""},
        {"subject": "no body"},
    ],
)
def test_invalid_tickets_rejected(client_factory, payload):
    with client_factory(ScriptedClient()) as client:
        assert client.post("/api/tickets", json=payload).status_code == 422


def test_second_feedback_is_rejected_and_not_double_counted(client_factory):
    llm = ScriptedClient(triage=make_triage(), draft=make_draft())
    with client_factory(llm) as client:
        tid = client.post("/api/tickets", json=TICKET).json()["id"]
        assert client.post(f"/api/tickets/{tid}/feedback", json={"action": "rejected"}).status_code == 200
        assert client.post(f"/api/tickets/{tid}/feedback", json={"action": "rejected"}).status_code == 409
        assert client.get("/api/metrics").json()["agent_feedback"]["total"] == 1


def test_feedback_on_unknown_ticket_404(client_factory):
    with client_factory(ScriptedClient()) as client:
        assert client.post("/api/tickets/999/feedback", json={"action": "rejected"}).status_code == 404


def test_ui_pages_render(client_factory):
    llm = ScriptedClient(triage=make_triage(), draft=make_draft())
    with client_factory(llm) as client:
        tid = client.post("/api/tickets", json=TICKET).json()["id"]
        for path in ("/", "/tickets/new", f"/tickets/{tid}", "/metrics", "/healthz"):
            assert client.get(path).status_code == 200, path
