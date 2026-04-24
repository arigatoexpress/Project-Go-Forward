"""
Chat History System for THO AI Agent

Provides full conversation persistence with:
- Complete message history (user + AI)
- Session management
- Conversation search and filtering
- Admin review capabilities
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from google.cloud import firestore

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """A single chat message"""
    role: str  # "user" or "model"
    text: str
    timestamp: str
    message_id: str = None
    
    def __post_init__(self):
        if self.message_id is None:
            self.message_id = f"msg_{datetime.utcnow().timestamp()}"
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ChatMessage':
        return cls(**data)


@dataclass
class ChatSession:
    """A complete chat session with full history"""
    session_id: str
    user_id: str
    messages: List[ChatMessage]
    created_at: str
    updated_at: str
    metadata: Dict[str, Any]
    status: str  # "active", "closed", "converted"
    lead_id: Optional[str] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow().isoformat()
        if self.updated_at is None:
            self.updated_at = datetime.utcnow().isoformat()
        if self.messages is None:
            self.messages = []
        if self.metadata is None:
            self.metadata = {}
        if self.status is None:
            self.status = "active"
    
    def add_message(self, role: str, text: str) -> ChatMessage:
        """Add a new message to the session"""
        message = ChatMessage(role=role, text=text, timestamp=datetime.utcnow().isoformat())
        self.messages.append(message)
        self.updated_at = datetime.utcnow().isoformat()
        return message
    
    def to_dict(self) -> Dict:
        return {
            'session_id': self.session_id,
            'user_id': self.user_id,
            'messages': [m.to_dict() for m in self.messages],
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'metadata': self.metadata,
            'status': self.status,
            'lead_id': self.lead_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ChatSession':
        data['messages'] = [ChatMessage.from_dict(m) for m in data.get('messages', [])]
        return cls(**data)
    
    def get_summary(self) -> str:
        """Get a summary of the conversation"""
        user_msgs = [m for m in self.messages if m.role == "user"]
        ai_msgs = [m for m in self.messages if m.role == "model"]
        return f"{len(user_msgs)} user messages, {len(ai_msgs)} AI responses"


class ChatHistory:
    """Manages chat history using Firestore"""
    
    def __init__(self, project_id: str = None):
        """Initialize Firestore client"""
        self.db = firestore.Client(project=project_id)
        self.collection_name = "chat_sessions"
    
    async def get_or_create_session(
        self, 
        session_id: str, 
        user_id: str,
        metadata: Dict = None
    ) -> ChatSession:
        """Get existing session or create new one"""
        doc_ref = self.db.collection(self.collection_name).document(session_id)
        doc = doc_ref.get()
        
        if doc.exists:
            return ChatSession.from_dict(doc.to_dict())
        else:
            # Create new session
            session = ChatSession(
                session_id=session_id,
                user_id=user_id,
                messages=[],
                created_at=datetime.utcnow().isoformat(),
                updated_at=datetime.utcnow().isoformat(),
                metadata=metadata or {},
                status="active"
            )
            await self.save_session(session)
            return session
    
    async def save_session(self, session: ChatSession):
        """Save chat session to Firestore"""
        try:
            doc_ref = self.db.collection(self.collection_name).document(session.session_id)
            doc_ref.set(session.to_dict())
            logger.info(f"Saved chat session {session.session_id} with {len(session.messages)} messages")
        except Exception as e:
            logger.error(f"Failed to save chat session: {e}")
            raise
    
    async def add_message(
        self, 
        session_id: str, 
        user_id: str, 
        role: str, 
        text: str,
        metadata: Dict = None
    ) -> ChatMessage:
        """Add a message to a session"""
        session = await self.get_or_create_session(session_id, user_id, metadata)
        message = session.add_message(role, text)
        await self.save_session(session)
        return message
    
    async def get_session(self, session_id: str) -> Optional[ChatSession]:
        """Get a specific chat session"""
        doc_ref = self.db.collection(self.collection_name).document(session_id)
        doc = doc_ref.get()
        if doc.exists:
            return ChatSession.from_dict(doc.to_dict())
        return None
    
    async def get_user_sessions(
        self, 
        user_id: str, 
        limit: int = 10,
        status: str = None
    ) -> List[ChatSession]:
        """Get all sessions for a user"""
        query = self.db.collection(self.collection_name).where("user_id", "==", user_id)
        
        if status:
            query = query.where("status", "==", status)
        
        query = query.order_by("updated_at", direction=firestore.Query.DESCENDING).limit(limit)
        
        docs = query.stream()
        return [ChatSession.from_dict(d.to_dict()) for d in docs]
    
    async def get_recent_sessions(
        self, 
        hours: int = 24,
        limit: int = 100
    ) -> List[ChatSession]:
        """Get recent chat sessions (for admin review)"""
        since = datetime.utcnow() - timedelta(hours=hours)
        
        query = (
            self.db.collection(self.collection_name)
            .where("updated_at", ">=", since.isoformat())
            .order_by("updated_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        
        docs = query.stream()
        return [ChatSession.from_dict(d.to_dict()) for d in docs]
    
    async def update_session_status(
        self, 
        session_id: str, 
        status: str,
        lead_id: str = None
    ):
        """Update session status (e.g., when converted to lead)"""
        doc_ref = self.db.collection(self.collection_name).document(session_id)
        updates = {"status": status, "updated_at": datetime.utcnow().isoformat()}
        if lead_id:
            updates["lead_id"] = lead_id
        doc_ref.update(updates)
    
    async def get_context_for_agent(
        self, 
        session_id: str, 
        user_id: str,
        max_messages: int = 10
    ) -> List[Dict]:
        """Get recent messages formatted for the AI agent"""
        session = await self.get_or_create_session(session_id, user_id)
        
        # Get last N messages
        recent = session.messages[-max_messages:] if len(session.messages) > max_messages else session.messages
        
        # Format for Gemini API
        formatted = []
        for msg in recent:
            formatted.append({
                "role": msg.role,
                "parts": [{"text": msg.text}]
            })
        
        return formatted
    
    async def search_conversations(
        self, 
        query: str,
        limit: int = 20
    ) -> List[Dict]:
        """Search through conversation history (admin feature)"""
        # Note: This is a simple implementation. For production,
        # consider using Algolia or Firestore full-text search
        
        sessions = []
        docs = self.db.collection(self.collection_name).limit(100).stream()
        
        for doc in docs:
            data = doc.to_dict()
            # Simple text search in messages
            messages = data.get('messages', [])
            for msg in messages:
                if query.lower() in msg.get('text', '').lower():
                    sessions.append({
                        'session_id': data['session_id'],
                        'user_id': data['user_id'],
                        'created_at': data['created_at'],
                        'message_preview': msg['text'][:100] + "..." if len(msg['text']) > 100 else msg['text'],
                        'status': data.get('status', 'unknown')
                    })
                    break
            
            if len(sessions) >= limit:
                break
        
        return sessions
