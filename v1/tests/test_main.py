"""
Tests for Project Go Forward v1 - Core API
"""

import pytest
from fastapi.testclient import TestClient

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app, _sessions, _leads

client = TestClient(app)


class TestHealthEndpoint:
    """Tests for the health check endpoint."""
    
    def test_health_returns_200(self):
        response = client.get("/health")
        assert response.status_code == 200
    
    def test_health_returns_correct_structure(self):
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert "version" in data
        assert "timestamp" in data
        assert data["status"] == "healthy"
    
    def test_health_returns_semantic_version(self):
        response = client.get("/health")
        data = response.json()
        version_parts = data["version"].split(".")
        assert len(version_parts) >= 2


class TestRootEndpoint:
    """Tests for the root endpoint."""
    
    def test_root_returns_200(self):
        response = client.get("/")
        assert response.status_code == 200
    
    def test_root_contains_api_info(self):
        response = client.get("/")
        data = response.json()
        assert "name" in data
        assert "version" in data
        assert "docs" in data


class TestChatEndpoint:
    """Tests for the chat endpoint."""
    
    def test_chat_returns_200(self):
        response = client.post("/chat", json={"message": "Hello"})
        assert response.status_code == 200
    
    def test_chat_creates_session(self):
        response = client.post("/chat", json={"message": "Hello"})
        data = response.json()
        assert "session_id" in data
        assert "response" in data
        assert len(data["session_id"]) > 0
    
    def test_chat_uses_provided_session_id(self):
        session_id = "test-session-123"
        response = client.post("/chat", json={
            "message": "Hello",
            "session_id": session_id
        })
        data = response.json()
        assert data["session_id"] == session_id
    
    def test_chat_responds_to_greeting(self):
        response = client.post("/chat", json={"message": "Hello there"})
        data = response.json()
        assert "hello" in data["response"].lower() or "welcome" in data["response"].lower()
    
    def test_chat_empty_message_returns_422(self):
        response = client.post("/chat", json={"message": ""})
        assert response.status_code == 422
    
    def test_chat_message_too_long_returns_422(self):
        long_message = "x" * 4001
        response = client.post("/chat", json={"message": long_message})
        assert response.status_code == 422


class TestLeadEndpoint:
    """Tests for the lead capture endpoint."""
    
    def test_capture_lead_returns_200(self):
        response = client.post("/leads", json={
            "name": "John Doe",
            "email": "john@example.com",
            "phone": "555-1234",
            "interest": "Product inquiry"
        })
        assert response.status_code == 200
    
    def test_capture_lead_returns_lead_id(self):
        response = client.post("/leads", json={
            "name": "Jane Smith",
            "interest": "Service inquiry"
        })
        data = response.json()
        assert "lead_id" in data
        assert data["status"] == "captured"
    
    def test_capture_lead_stores_data(self):
        initial_count = len(_leads)
        client.post("/leads", json={
            "name": "Test User",
            "interest": "Testing"
        })
        assert len(_leads) == initial_count + 1
    
    def test_capture_lead_missing_name_returns_422(self):
        response = client.post("/leads", json={
            "interest": "No name provided"
        })
        assert response.status_code == 422
    
    def test_list_leads_returns_leads(self):
        response = client.get("/leads")
        assert response.status_code == 200
        data = response.json()
        assert "leads" in data
        assert "count" in data


class TestSessionEndpoint:
    """Tests for the session retrieval endpoint."""
    
    def test_get_existing_session(self):
        # First create a session
        chat_response = client.post("/chat", json={"message": "Hello"})
        session_id = chat_response.json()["session_id"]
        
        # Then retrieve it
        response = client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert "messages" in data
    
    def test_get_nonexistent_session_returns_404(self):
        response = client.get("/sessions/nonexistent-session-xyz")
        assert response.status_code == 404


class TestIntegration:
    """Integration tests for the full flow."""
    
    def test_full_conversation_flow(self):
        # Start conversation
        chat1 = client.post("/chat", json={"message": "Hello"})
        session_id = chat1.json()["session_id"]
        
        # Continue conversation
        chat2 = client.post("/chat", json={
            "message": "I'm interested in pricing",
            "session_id": session_id
        })
        assert chat2.status_code == 200
        
        # Capture lead
        lead = client.post("/leads", json={
            "name": "Integration Test User",
            "email": "test@example.com",
            "interest": "Pricing inquiry from chat"
        })
        assert lead.status_code == 200
        
        # Verify session history
        session = client.get(f"/sessions/{session_id}")
        assert session.status_code == 200
        assert len(session.json()["messages"]) >= 2
    
    def test_health_under_load(self):
        """Quick health check under multiple requests."""
        for _ in range(5):
            response = client.get("/health")
            assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
