"""
Tests for Project Go Forward v1
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app, _sessions, _leads

client = TestClient(app)


class TestHealth:
    def test_health_check(self):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"
        assert "timestamp" in data
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0


class TestRoot:
    def test_root_endpoint(self):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Project Go Forward v1"
        assert data["version"] == "1.0.0"
        assert "endpoints" in data


class TestChat:
    def test_chat_new_session(self):
        response = client.post("/chat", json={"message": "Hello"})
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "response" in data
        assert "timestamp" in data
        assert "Hello" in data["response"] or "help" in data["response"].lower()

    def test_chat_existing_session(self):
        # First message creates session
        r1 = client.post("/chat", json={"message": "Hello"})
        session_id = r1.json()["session_id"]
        
        # Second message uses same session
        r2 = client.post("/chat", json={"session_id": session_id, "message": "Tell me about pricing"})
        assert r2.status_code == 200
        assert r2.json()["session_id"] == session_id

    def test_chat_pricing_response(self):
        response = client.post("/chat", json={"message": "What's the price?"})
        assert response.status_code == 200
        data = response.json()
        assert "price" in data["response"].lower() or "pricing" in data["response"].lower()

    def test_chat_validation_empty_message(self):
        response = client.post("/chat", json={"message": ""})
        assert response.status_code == 422

    def test_chat_validation_message_too_long(self):
        response = client.post("/chat", json={"message": "x" * 4001})
        assert response.status_code == 422


class TestSessions:
    def test_get_session(self):
        # Create a session first
        r1 = client.post("/chat", json={"message": "Hello"})
        session_id = r1.json()["session_id"]
        
        # Get the session
        r2 = client.get(f"/sessions/{session_id}")
        assert r2.status_code == 200
        data = r2.json()
        assert data["session_id"] == session_id
        assert "messages" in data
        assert len(data["messages"]) == 2  # user + assistant

    def test_get_session_not_found(self):
        response = client.get("/sessions/nonexistent-id")
        assert response.status_code == 404


class TestLeads:
    def setup_method(self):
        # Clear leads before each test
        _leads.clear()

    def test_create_lead(self):
        response = client.post("/leads", json={
            "name": "John Doe",
            "email": "john@example.com",
            "phone": "555-123-4567",
            "interest": "Looking for a 3 bedroom home"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "captured"
        assert "lead_id" in data
        assert "timestamp" in data

    def test_create_lead_minimal(self):
        response = client.post("/leads", json={
            "name": "Jane Doe",
            "interest": "General inquiry"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "captured"

    def test_create_lead_validation_missing_name(self):
        response = client.post("/leads", json={
            "interest": "General inquiry"
        })
        assert response.status_code == 422

    def test_create_lead_validation_missing_interest(self):
        response = client.post("/leads", json={
            "name": "John Doe"
        })
        assert response.status_code == 422

    def test_list_leads(self):
        # Create a lead
        client.post("/leads", json={
            "name": "John Doe",
            "interest": "Test"
        })
        
        # List leads
        response = client.get("/leads")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "John Doe"


class TestIntegration:
    def setup_method(self):
        # Clear leads before each test to ensure isolation
        _leads.clear()

    def test_full_conversation_flow(self):
        """Test a full conversation leading to lead capture."""
        # Start conversation
        r1 = client.post("/chat", json={"message": "Hi, I'm interested in pricing"})
        assert r1.status_code == 200
        session_id = r1.json()["session_id"]
        
        # Continue conversation
        r2 = client.post("/chat", json={
            "session_id": session_id,
            "message": "Can someone call me?"
        })
        assert r2.status_code == 200
        
        # Capture lead
        r3 = client.post("/leads", json={
            "name": "Test User",
            "phone": "555-999-8888",
            "interest": "Pricing inquiry from chat"
        })
        assert r3.status_code == 200
        
        # Verify lead was captured
        leads = client.get("/leads").json()
        assert len(leads) == 1
        assert leads[0]["name"] == "Test User"
