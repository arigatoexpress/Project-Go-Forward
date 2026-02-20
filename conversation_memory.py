"""
Conversation Memory System for THO AI Agent
Tracks user preferences and conversation context within a session
"""

import json
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from google.cloud import firestore

logger = logging.getLogger(__name__)

@dataclass
class UserPreferences:
    """User preferences extracted from conversation"""
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    min_budget: Optional[float] = None
    max_budget: Optional[float] = None
    home_type: Optional[str] = None  # "Single Wide", "Double Wide", "Modular"
    preferred_models: List[str] = None
    location_preferences: List[str] = None
    
    def __post_init__(self):
        if self.preferred_models is None:
            self.preferred_models = []
        if self.location_preferences is None:
            self.location_preferences = []
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'UserPreferences':
        """Create from dictionary"""
        return cls(**data)
    
    def to_prompt_context(self) -> str:
        """Generate context string for agent prompt"""
        parts = []
        if self.bedrooms:
            parts.append(f"{self.bedrooms} bedroom{'s' if self.bedrooms > 1 else ''}")
        if self.bathrooms:
            parts.append(f"{self.bathrooms} bathroom{'s' if self.bathrooms > 1 else ''}")
        if self.max_budget:
            parts.append(f"budget up to ${int(self.max_budget):,}")
        if self.home_type:
            parts.append(f"{self.home_type} homes")
        
        if parts:
            return f"User is looking for: " + ", ".join(parts)
        return ""


@dataclass
class ConversationContext:
    """Full conversation context for a session"""
    session_id: str
    user_id: str
    preferences: UserPreferences
    last_search_query: Optional[Dict] = None
    homes_discussed: List[str] = None  # List of model names or serial numbers
    appointment_intent: bool = False
    financing_questions: int = 0
    created_at: str = None
    updated_at: str = None
    
    def __post_init__(self):
        if self.homes_discussed is None:
            self.homes_discussed = []
        if self.created_at is None:
            self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()
    
    def update_from_message(self, message: str, search_results: Optional[List[Dict]] = None):
        """Update context based on user message and any search results"""
        message_lower = message.lower()
        
        # Extract bedroom preferences
        if 'bedroom' in message_lower or ' bed' in message_lower:
            for num in range(1, 7):
                if str(num) in message or self._number_word_to_int(message_lower) == num:
                    self.preferences.bedrooms = num
                    break
        
        # Extract bathroom preferences
        if 'bathroom' in message_lower or ' bath' in message_lower:
            for num in range(1, 5):
                if str(num) in message or self._number_word_to_int(message_lower) == num:
                    self.preferences.bathrooms = num
                    break
        
        # Extract budget
        if '$' in message or 'budget' in message_lower or 'price' in message_lower:
            import re
            amounts = re.findall(r'\$?([\d,]+)k?', message)
            for amount in amounts:
                try:
                    value = float(amount.replace(',', ''))
                    if value < 1000:  # Assume it's in thousands
                        value *= 1000
                    if 'under' in message_lower or 'less than' in message_lower or 'max' in message_lower:
                        self.preferences.max_budget = value
                    elif 'over' in message_lower or 'more than' in message_lower or 'min' in message_lower:
                        self.preferences.min_budget = value
                    else:
                        self.preferences.max_budget = value
                except ValueError:
                    pass
        
        # Extract home type
        if 'single wide' in message_lower:
            self.preferences.home_type = "Single Wide"
        elif 'double wide' in message_lower:
            self.preferences.home_type = "Double Wide"
        elif 'modular' in message_lower:
            self.preferences.home_type = "Modular"
        
        # Track appointment intent
        if any(word in message_lower for word in ['appointment', 'visit', 'see it', 'tour', 'showing']):
            self.appointment_intent = True
        
        # Track financing questions
        if any(word in message_lower for word in ['payment', 'finance', 'loan', 'rate', 'monthly']):
            self.financing_questions += 1
        
        # Track homes discussed
        if search_results:
            for home in search_results[:3]:  # Top 3 results
                model = home.get('model', '')
                if model and model not in self.homes_discussed:
                    self.homes_discussed.append(model)
        
        self.updated_at = datetime.utcnow().isoformat()
    
    def _number_word_to_int(self, text: str) -> Optional[int]:
        """Convert number words to integers"""
        number_words = {
            'one': 1, 'two': 2, 'three': 3, 'four': 4, 
            'five': 5, 'six': 6, 'seven': 7, 'eight': 8
        }
        for word, num in number_words.items():
            if word in text:
                return num
        return None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage"""
        return {
            'session_id': self.session_id,
            'user_id': self.user_id,
            'preferences': self.preferences.to_dict(),
            'last_search_query': self.last_search_query,
            'homes_discussed': self.homes_discussed,
            'appointment_intent': self.appointment_intent,
            'financing_questions': self.financing_questions,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ConversationContext':
        """Create from dictionary"""
        data['preferences'] = UserPreferences.from_dict(data.get('preferences', {}))
        return cls(**data)


class ConversationMemory:
    """Manages conversation memory using Firestore"""
    
    def __init__(self, project_id: str = None):
        """Initialize Firestore client"""
        self.db = firestore.Client(project=project_id)
        self.collection_name = "conversation_contexts"
    
    async def get_context(self, session_id: str, user_id: str) -> ConversationContext:
        """Retrieve or create conversation context"""
        doc_ref = self.db.collection(self.collection_name).document(session_id)
        doc = doc_ref.get()
        
        if doc.exists:
            return ConversationContext.from_dict(doc.to_dict())
        else:
            # Create new context
            context = ConversationContext(
                session_id=session_id,
                user_id=user_id,
                preferences=UserPreferences()
            )
            return context
    
    async def save_context(self, context: ConversationContext):
        """Save conversation context to Firestore"""
        doc_ref = self.db.collection(self.collection_name).document(context.session_id)
        doc_ref.set(context.to_dict())
    
    async def update_from_interaction(
        self, 
        session_id: str, 
        user_id: str, 
        user_message: str,
        search_results: Optional[List[Dict]] = None
    ) -> ConversationContext:
        """Update context based on user interaction"""
        context = await self.get_context(session_id, user_id)
        context.update_from_message(user_message, search_results)
        await self.save_context(context)
        return context
    
    async def get_context_prompt(self, session_id: str, user_id: str) -> str:
        """Get context string to inject into agent prompt."""
        try:
            context = await self.get_context(session_id, user_id)

            parts = []
            pref_context = context.preferences.to_prompt_context()
            if pref_context:
                parts.append(pref_context)

            if context.homes_discussed:
                parts.append(f"Previously discussed: {', '.join(context.homes_discussed[:3])}")

            if context.appointment_intent:
                parts.append("User has shown interest in scheduling an appointment")

            if context.financing_questions > 0:
                parts.append(f"User has asked {context.financing_questions} financing question(s)")

            if parts:
                return "\n\nConversation Context:\n" + "\n".join(f"- {p}" for p in parts)
            return ""
        except Exception as e:
            logger.warning("Error getting context prompt: %s", e)
            return ""
