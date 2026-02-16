# 🚀 Project Go Forward

**A config-driven, multi-agent AI assistant framework built on Google ADK (Agent Development Kit).**

Turn any business into an AI-powered operation with conversational sales, service, and lead management — in minutes, not months.

> See `AGENTS.md` for agentic navigation.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🤖 **Multi-Agent System** | Root agent routes to specialized Sales and Service sub-agents |
| 🔧 **Config-Driven** | Edit `config.yaml` once — business name, address, products, branding all update automatically |
| 📦 **Structured Output** | Pydantic `output_schema` ensures guaranteed JSON responses (no parsing headaches) |
| 🎨 **Modern Chat UI** | React frontend with property/product cards, image galleries, comparison drawer, search filters |
| 📊 **Lead Management** | Auto-captures leads from conversations with CRM integration |
| 🗄️ **Firestore Database** | Cloud-native data storage with Pydantic models |
| 🌐 **Web Scraper** | Configurable scraper to populate inventory from any website |
| 🚀 **Cloud Run Ready** | Dockerfile included, deploys to Google Cloud Run in one command |
| 📈 **Analytics** | Structured logging, conversation memory, and lead statistics |

---

## 🏗️ Architecture

```
┌─────────────────────────────────┐
│         React Frontend          │
│   (Chat UI, Product Cards)      │
└─────────────┬───────────────────┘
              │ HTTP /run
┌─────────────▼───────────────────┐
│      FastAPI Server (main.py)   │
│  Sessions, Leads, SPA Hosting   │
└─────────────┬───────────────────┘
              │ ADK Runner
┌─────────────▼───────────────────┐
│     Root Agent (Router)         │
│   "How can I help you today?"   │
├────────────┬────────────────────┤
│ Sales Agent│   Service Agent    │
│ - Search   │   - Warranty       │
│ - Payment  │   - Work Orders    │
│ - Booking  │   - Defect Photos  │
│ - Leads    │   - Tickets        │
└────────────┴────────────────────┘
              │
┌─────────────▼───────────────────┐
│    config.yaml (all settings)   │
│    config_loader.py (accessor)  │
└─────────────────────────────────┘
```

---

## ⚡ Quick Start

### 1. Clone & Configure

```bash
git clone https://github.com/arigatoexpress/Project-Go-Forward.git
cd Project-Go-Forward

# Edit the single config file with YOUR business details
nano config.yaml
```

### 2. Set Up Environment

```bash
# Create .env from template
cp .env.example .env
# Edit with your GCP project ID
nano .env

# Install Python dependencies
pip install -r requirements.txt

# Install frontend dependencies
cd frontend && npm install && cd ..
```

### 3. Add Your Inventory

```bash
# Copy the example and add your real data
cp data/inventory.example.json data/inventory.json
# Or use the scraper to populate from your website:
python tools/scraper.py
```

### 4. Run Locally

```bash
# Build frontend
cd frontend && npm run build && cd ..
cp -r frontend/dist frontend_build

# Start server
python main.py
# Visit http://localhost:8080
```

### 5. Deploy to Cloud Run

```bash
export GCP_PROJECT_ID="your-gcp-project-id"

gcloud run deploy project-go-forward \
  --source . \
  --region us-central1 \
  --project "$GCP_PROJECT_ID" \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT="$GCP_PROJECT_ID",GOOGLE_CLOUD_LOCATION=us-central1 \
  --memory 1Gi
```

---

## 📁 Project Structure

```
project-go-forward/
├── config.yaml              # 🎯 EDIT THIS — all business configuration
├── config_loader.py         # Reads config.yaml, provides typed accessors
├── root_agent.py            # Multi-agent system (root → sales, service)
├── main.py                  # FastAPI server with session/lead management
├── Dockerfile               # Cloud Run deployment
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
│
├── schemas/
│   ├── output_schemas.py    # Pydantic BaseModel for ADK output_schema
│   ├── inventory_schema.json
│   ├── customer_schema.json
│   └── service_request_schema.json
│
├── tools/
│   ├── inventory_tools.py   # Search, payment calculation
│   ├── crm_tools.py         # Appointments, business hours, leads
│   ├── service_tools.py     # Warranty, defect analysis
│   ├── document_tools.py    # PDF generation, emails
│   ├── marketing_tools.py   # Content, social media
│   ├── scraper.py           # Web scraper for inventory
│   └── merge_inventory.py   # Merge scraped data with existing
│
├── database/
│   ├── firestore_client.py  # Firestore CRUD operations
│   ├── models.py            # Pydantic data models
│   └── import_data.py       # Data import utilities
│
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # Main chat application
│   │   ├── components/
│   │   │   ├── PropertyCard.jsx
│   │   │   ├── SearchFilters.jsx
│   │   │   ├── ComparisonDrawer.jsx
│   │   │   └── QuickActions.jsx
│   │   └── pages/
│   │       └── Analytics.jsx
│   └── package.json
│
├── data/
│   └── inventory.example.json  # Sample inventory schema
│
├── tests/                   # Test suite
├── conversation_memory.py   # Session context tracking
├── lead_management.py       # Lead capture and CRM
├── structured_logging.py    # JSON structured logging
├── analytics_service.py     # Usage analytics
└── caching.py              # Response caching
```

---

## 🔧 Customization Guide

### Changing Business Identity

Edit `config.yaml`:
```yaml
business:
  name: "Acme Auto Sales"
  address: "456 Oak Drive, Austin, TX 78701"
  phone: "(512) 555-1234"
  hours:
    weekday: "Mon-Fri 8-7"
    saturday: "Sat 9-6"
    sunday: "Sun 11-4"
```

### Changing Product Type

```yaml
product:
  type: "vehicles"
  singular: "vehicle"
  plural: "vehicles"
  spec_fields:
    - key: "year"
      label: "Year"
    - key: "mileage"
      label: "Mileage"
    - key: "engine"
      label: "Engine"
```

### Using Structured Output (ADK Best Practice)

```python
from schemas.output_schemas import SearchResponse

sales_agent = LlmAgent(
    model="gemini-2.5-flash",
    output_schema=SearchResponse,  # Guarantees JSON structure
    output_key="search_results",   # Stores in session state
    instruction="Search inventory and return structured results..."
)
```

---

## 🔑 Key Technologies

- **Google ADK** (Agent Development Kit) — Multi-agent orchestration
- **Gemini 2.0 Flash** — LLM via Vertex AI
- **FastAPI** — Python web framework
- **React + Vite** — Frontend
- **Google Cloud Firestore** — Database
- **Google Cloud Run** — Serverless deployment
- **Pydantic** — Structured data validation

---

## 📜 License

MIT License — use freely for any business.

---

Built with ❤️ by [Kadima Digital Strategies](https://github.com/arigatoexpress)
