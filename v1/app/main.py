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
def root():
    """API info and available endpoints."""
    return {
        "name": "Project Go Forward v1",
        "version": VERSION,
        "endpoints": {
            "health": "/health",
            "chat": "POST /chat",
            "leads": "POST /leads",
            "docs": "/docs",
        },
    }


@app.get("/health", response_model=HealthResponse)
def health():
    """Health check with version and uptime."""
    return HealthResponse(
        uptime_seconds=round(time.time() - _start_time, 2)
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Send a message and get an AI-like response."""
    session_id = request.session_id or str(uuid.uuid4())
    
    # Initialize session if new
    if session_id not in _sessions:
        _sessions[session_id] = []
    
    # Store user message
    _sessions[session_id].append({
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    
    # Simple response logic (v1 - no AI integration yet)
    user_msg = request.message.lower()
    if "price" in user_msg or "cost" in user_msg:
        response = "I'd be happy to discuss pricing. Could you share your phone number so a sales representative can call you?"
    elif "hello" in user_msg or "hi" in user_msg:
        response = "Hello! Welcome to Project Go Forward. How can I help you today?"
    elif "help" in user_msg:
        response = "I can help you with general inquiries. For specific questions, I can connect you with a specialist."
    else:
        response = "Thank you for your message. A team member will follow up with you shortly."
    
    # Store assistant response
    _sessions[session_id].append({
        "role": "assistant",
        "content": response,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    
    return ChatResponse(
        session_id=session_id,
        response=response,
    )


@app.get("/sessions/{session_id}", response_model=dict)
def get_session(session_id: str):
    """Get conversation history for a session."""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "messages": _sessions[session_id],
    }


@app.post("/leads", response_model=LeadResponse)
def create_lead(lead: LeadCapture):
    """Capture a lead for business follow-up."""
    lead_id = str(uuid.uuid4())
    lead_data = {
        "lead_id": lead_id,
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "interest": lead.interest,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _leads.append(lead_data)
    return LeadResponse(lead_id=lead_id)


@app.get("/leads", response_model=list)
def list_leads():
    """List all captured leads."""
    return _leads


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
