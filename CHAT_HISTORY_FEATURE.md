# Chat History & Memory Feature

## Overview

Full conversation persistence system that saves all chat interactions between users and the AI agent, enabling:
- Complete conversation review by admins
- Conversation search and filtering
- Better context understanding for training
- Customer service quality monitoring

## Architecture

### Backend Components

**1. `chat_history.py`** - Core persistence layer
- `ChatMessage` - Individual message (role, text, timestamp)
- `ChatSession` - Complete conversation container
- `ChatHistory` - Firestore operations manager

**2. API Endpoints** (Admin only)
```
GET  /api/chat/history/{session_id}  - Get full conversation
GET  /api/chat/sessions              - List recent sessions
POST /api/chat/search                - Search conversations
```

**3. Integration**
- Messages saved during `/run` agent calls
- Automatic session creation on first message
- User ID and session ID tracked

### Frontend Components

**`ChatHistory.jsx`** - Admin UI
- Session list with filters (24h, 3d, 7d, 30d)
- Real-time search across conversations
- Message bubble view (user/AI)
- Lead conversion tracking
- Link to CRM for converted leads

## Data Model

### ChatSession Document
```json
{
  "session_id": "uuid",
  "user_id": "anonymous_or_logged_in",
  "messages": [
    {"role": "user", "text": "...", "timestamp": "..."},
    {"role": "model", "text": "...", "timestamp": "..."}
  ],
  "created_at": "ISO timestamp",
  "updated_at": "ISO timestamp",
  "status": "active|closed|converted",
  "lead_id": "lead_uuid",
  "metadata": {}
}
```

## Usage

### For Admins

1. Navigate to **Chat History** from admin menu
2. View recent conversations (default: last 24h)
3. Search for specific topics or questions
4. Click session to view full conversation
5. Converted leads show link to CRM

### For Developers

```python
# Save messages automatically (happens in /run endpoint)
await chat_history.add_message(session_id, user_id, "user", text)
await chat_history.add_message(session_id, user_id, "model", response)

# Retrieve session
session = await chat_history.get_session(session_id)

# Search conversations
results = await chat_history.search_conversations("financing")
```

## Privacy & Security

- All chat history admin-only access
- PII redaction applied before storage
- Session IDs anonymized
- 90-day retention (configurable)

## Benefits

1. **Customer Service** - Review conversations for quality
2. **Training Data** - Improve AI responses
3. **Lead Insights** - Understand customer journey
4. **Issue Resolution** - Debug conversation problems
5. **Compliance** - Maintain conversation records

## Future Enhancements

- [ ] Export conversations to CSV
- [ ] Conversation analytics dashboard
- [ ] Sentiment analysis
- [ ] Auto-tagging by topic
- [ ] Integration with CRM timeline
