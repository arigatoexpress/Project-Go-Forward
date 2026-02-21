"""
Project Go Forward v1 - Core FastAPI Application
A minimal, deployable vertical slice of the THO business clone.
"""

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Configuration from environment
PORT = int(os.environ.get("PORT", 8080))
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
VERSION = "1.0.0"

app = FastAPI(
    title="Project Go Forward v1",
    description="THO Business Clone - Core Product Slice",
    version=VERSION,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if DEBUG else [],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# Pydantic Models
class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = VERSION
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    uptime_seconds: float = 0.0


class ChatMessage(BaseModel):
    role: str = Field(..., description="User or assistant")
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str = Field(..., min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    session_id: str
    response: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LeadCapture(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: Optional[str] = None
    phone: Optional[str] = None
    interest: str = Field(..., min_length=1, max_length=200)


class LeadResponse(BaseModel):
    lead_id: str
    status: str = "captured"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# In-memory storage (for v1 vertical slice)
_sessions: dict = {}
_leads: list = []
_start_time = time.time()


@app.get("/", response_model=dict)
async def root():
    """Root endpoint - API info."""
    return {
        "name": "Project Go Forward v1",
        "version": VERSION,
        "status": "operational",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint for load balancers and monitoring."""
    return HealthResponse(
        status="healthy",
        version=VERSION,
        uptime_seconds=time.time() - _start_time,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Simple chat endpoint - captures messages and returns basic responses.
    This is the core interaction for the THO business clone v1.
    """
    session_id = request.session_id or str(uuid.uuid4())[:8]
    
    # Store message in session history
    if session_id not in _sessions:
        _sessions[session_id] = []
    
    _sessions[session_id].append({
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    
    # Simple response logic for v1 (replace with ADK integration in v2)
    response_text = _generate_response(request.message)
    
    _sessions[session_id].append({
        "role": "assistant",
        "content": response_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    
    return ChatResponse(
        session_id=session_id,
        response=response_text,
    )


def _generate_response(message: str) -> str:
    """Generate a simple response based on user input (v1 placeholder)."""
    message_lower = message.lower()
    
    if any(word in message_lower for word in ["hello", "hi", "hey"]):
        return "Hello! Welcome to our business. How can I help you today?"
    
    if any(word in message_lower for word in ["price", "cost", "how much"]):
        return "I'd be happy to discuss pricing with you. Could you share more details about what you're looking for?"
    
    if any(word in message_lower for word in ["book", "appointment", "schedule"]):
        return "I can help you schedule an appointment. What date and time works best for you?"
    
    if any(word in message_lower for word in ["product", "service", "inventory"]):
        return "We offer a variety of products and services. What specific area are you interested in?"
    
    if any(word in message_lower for word in ["help", "support"]):
        return "I'm here to help! I can assist with product questions, scheduling, or capturing your contact info for our team."
    
    return "Thank you for your message. I'll make sure our team gets back to you. Would you like to leave your contact information?"


@app.post("/leads", response_model=LeadResponse)
async def capture_lead(lead: LeadCapture):
    """Capture a lead from user interactions."""
    lead_id = str(uuid.uuid4())[:12]
    
    lead_data = {
        "lead_id": lead_id,
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "interest": lead.interest,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "new",
    }
    
    _leads.append(lead_data)
    
    return LeadResponse(lead_id=lead_id, status="captured")


@app.get("/leads")
async def list_leads():
    """List all captured leads (for admin/debug purposes)."""
    return {"leads": _leads, "count": len(_leads)}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get conversation history for a session."""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "messages": _sessions[session_id]}


def main():
    """Entry point for running the application."""
    import uvicorn
    
    print(f"🚀 Starting Project Go Forward v{VERSION}")
    print(f"📍 Server: http://{os.environ.get('HOST', '0.0.0.0')}:{PORT}")
    print(f"📚 Docs: http://localhost:{PORT}/docs")
    
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=PORT,
        reload=DEBUG,
    )


if __name__ == "__main__":
    main()
