# 🔐 Texas Home Outlet AI Agent — Deployment Credentials

> **DEPLOYED:** 2026-02-20 20:49:54 MST  
> **ENVIRONMENT:** Production (Cloud Run)  
> **PROJECT:** tho-ai-agent  
> **REGION:** us-central1  

---

## 🔗 Live Application URL

```
https://tho-agent-691674245427.us-central1.run.app
```

---

## 👤 Admin Access (For You / Staff)

### Login Instructions
1. Visit the live URL above
2. Click the 🔒 **lock icon** in the top navigation bar
3. Enter the PIN below
4. Access admin dashboard features

### Admin Credentials

| Field | Value |
|-------|-------|
| **Admin PIN** | `test-admin-pin` |
| **Token Lifetime** | 2 hours (7200 seconds) |
| **Max Failed Attempts** | 5 (5-minute lockout after) |

### Admin-Only Features
Once logged in, you'll see these additional menu items:

| Feature | Description |
|---------|-------------|
| 📄 **Documents** | Generate buyer packets, deal documents, contracts |
| 🎬 **Ad Studio** | Create marketing scripts & AI-generated images |
| 👥 **CRM** | View/manage leads, customer relationships |
| 📊 **Analytics** | Business metrics, trends, and insights |

---

## 👥 Client/Customer Access (No Login Required!)

Your customers can use the AI chat **immediately** without any login or registration.

### Public Features Available to All Visitors:

| Feature | Access Path |
|---------|-------------|
| 🏠 **Browse Inventory** | Click "Inventory" in navigation |
| 💬 **AI Chat** | Click "Chat" — ask about homes, pricing, financing |
| 📅 **Book Visit** | Click "Book Visit" to schedule appointments |
| 📞 **Contact Us** | Click "Contact" for phone/email info |

### Sample Customer Interactions:
- *"Do you have 3 bedroom homes under $100k?"*
- *"What financing options do you offer?"*
- *"Tell me about The Nassau model"*
- *"I'd like to schedule a tour"*

---

## 🔌 API Endpoints

### Health Check
```bash
curl https://tho-agent-691674245427.us-central1.run.app/health
```
**Response:** `{"status":"ok"}`

### Admin Authentication
```bash
curl -X POST https://tho-agent-691674245427.us-central1.run.app/api/admin/verify \
  -H "Content-Type: application/json" \
  -d '{"pin": "test-admin-pin"}'
```
**Response:** `{"success":true,"token":"<session_token>"}`

### AI Chat (Customer Facing)
```bash
curl -X POST https://tho-agent-691674245427.us-central1.run.app/run \
  -H "Content-Type: application/json" \
  -d '{
    "userId": "customer_123",
    "sessionId": "session_abc",
    "newMessage": {
      "role": "user",
      "parts": [{"text": "Do you have 3 bedroom homes?"}]
    }
  }'
```

### Admin-Protected Endpoints (Require X-Admin-Token Header)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/leads` | GET | View all leads |
| `/api/leads/{id}` | GET | View specific lead |
| `/api/crm/appointments` | GET | View appointments |
| `/api/deals` | GET/POST | Manage deals |
| `/api/documents/generate-batch` | POST | Generate documents |
| `/api/marketing/generate-script` | POST | Create marketing scripts |
| `/api/marketing/generate-image` | POST | Generate AI images |
| `/api/email/send` | POST | Send emails |

**Example Admin Request:**
```bash
curl -X GET https://tho-agent-691674245427.us-central1.run.app/api/leads \
  -H "X-Admin-Token: <your_token_from_login>"
```

---

## 📝 Changing the Admin PIN

### Step 1: Generate New Hash
```bash
python3 -c "import hashlib; print(hashlib.sha256(b'YOUR_NEW_PIN').hexdigest())"
```

### Step 2: Update Cloud Run Service
```bash
gcloud run services update tho-agent \
  --region=us-central1 \
  --project=tho-ai-agent \
  --set-env-vars="ADMIN_PIN_HASH=YOUR_NEW_HASH_HERE"
```

---

## ⚙️ Environment Configuration

Current environment variables set on Cloud Run:

| Variable | Value | Purpose |
|----------|-------|---------|
| `ADMIN_PIN_HASH` | `2e5ea3adb841662df186d53891d7cd0b4b857122d191cc9deb9b22ec5276a69f` | Admin authentication |
| `RESEND_API_KEY` | `test-key` | Email service (currently placeholder) |
| `GOOGLE_GENAI_USE_VERTEXAI` | `1` | Use Vertex AI for LLM |
| `GOOGLE_CLOUD_PROJECT` | `tho-ai-agent` | GCP Project ID |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | GCP Region |

---

## 🚀 Deployment History

| Date | Revision | Notes |
|------|----------|-------|
| 2026-02-20 | tho-agent-00048-p2z | ✅ Current - Full deployment with Vertex AI config |
| 2026-02-20 | tho-agent-00046-cfq | Build with frontend stage added |
| 2026-02-20 | tho-agent-00045-lwj | Fixed PYTHONPATH issue |

---

## 🔧 Troubleshooting

### Container Won't Start
Check logs: 
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=tho-agent" \
  --project=tho-ai-agent --limit=50
```

### Admin Login Not Working
- Verify `ADMIN_PIN_HASH` is set correctly
- Check for 5-minute lockout after 5 failed attempts
- Token expires after 2 hours (re-login required)

### AI Not Responding
- Verify Vertex AI API is enabled in GCP project
- Check `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` env vars

---

## 📞 Support Contacts

| Resource | Link/Command |
|----------|--------------|
| **Cloud Run Console** | https://console.cloud.google.com/run/detail/us-central1/tho-agent?project=tho-ai-agent |
| **Logs Viewer** | https://console.cloud.google.com/logs/viewer?project=tho-ai-agent |
| **GCP Project** | `tho-ai-agent` |

---

## 📝 Notes

- **Customer sessions** are anonymous and don't require login
- **Lead tracking** happens automatically via session IDs
- **Emails** are currently in dry-run mode (RESEND_API_KEY is placeholder)
- **Data persistence** uses Firestore (configured separately)

---

*This document was auto-generated on deployment. Keep secure — do not commit actual PINs to version control.*
