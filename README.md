# 🚀 Project Go Forward (THO)

**A deployable core product slice with reproducible local and cloud workflows.**

> **⚡ Quick Start:** Jump to [`v1/`](v1/) for the deployable vertical slice.

---

## 📂 Repository Structure

```
Project-Go-Forward/
├── v1/                    # 🎯 PRIMARY: Deployable vertical slice
│   ├── app/              # FastAPI backend (208 LOC)
│   ├── tests/            # 20 passing tests
│   ├── frontend/         # Simple status page
│   ├── deploy.sh         # Unified deployment script ⭐
│   ├── Dockerfile        # Container image
│   └── README.md         # v1 documentation
│
├── archive/              # 📦 Legacy business clone (archived)
│   ├── main.py           # Full application (~868 LOC)
│   ├── root_agent.py     # Multi-agent orchestration
│   └── README.md         # Archive notes
│
└── frontend/             # 🎨 React frontend (legacy, optional)
```

---

## 🎯 v1 Vertical Slice (Recommended)

The v1 directory contains a **minimal, tested, deployable** version ready for Cloud Run:

| Feature | Status |
|---------|--------|
| FastAPI Backend | ✅ 208 LOC, clean endpoints |
| Health Checks | ✅ For load balancers |
| Chat API | ✅ Session-based conversations |
| Lead Capture | ✅ POST/GET endpoints |
| Tests | ✅ 20 tests, all passing |
| Docker | ✅ Multi-stage build |
| Deploy Script | ✅ `./deploy.sh [local\|test\|deploy]` |
| Cloud Run | ✅ Ready with traffic management |

### Quick Start (v1)

```bash
cd v1

# Setup
./deploy.sh setup

# Run tests
./deploy.sh test

# Run locally
./deploy.sh local

# Deploy to Cloud Run
GOOGLE_CLOUD_PROJECT=my-project ./deploy.sh pipeline
```

### API Endpoints (v1)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API info |
| `/health` | GET | Health check |
| `/chat` | POST | Send message |
| `/leads` | POST | Capture lead |
| `/leads` | GET | List leads |
| `/docs` | GET | Swagger UI |

See [`v1/README.md`](v1/README.md) for complete documentation.

---

## 📦 Archive (Legacy)

The `archive/` directory contains the original full business clone with:
- Multi-agent system (Sales/Service agents)
- Full appointment management
- Analytics service
- Firestore integration
- React frontend
- ~2,500+ lines of code

**Status:** Archived in favor of v1's minimal approach. See [`archive/README.md`](archive/README.md) for migration notes.

---

## 🏗️ Architecture Decision

| Approach | LOC | Deploy Time | Status |
|----------|-----|-------------|--------|
| Legacy (archive/) | ~2,500 | Complex | ❌ Archived |
| **v1 Vertical Slice** | **~208** | **5 min** | **✅ Active** |

v1 prioritizes:
1. **Immediate deployability** — Ship today, not someday
2. **Tested core** — 20 tests, all passing
3. **Clear upgrade path** — Add features incrementally
4. **Reproducible workflows** — Same script, local or cloud

---

## 🚀 Deployment Commands

### Local Development
```bash
cd v1
./deploy.sh local     # Start dev server on :8080
./deploy.sh test      # Run full test suite
```

### Cloud Deployment
```bash
cd v1
export GOOGLE_CLOUD_PROJECT=my-project

# Full pipeline: test → build → deploy
./deploy.sh pipeline

# Or step by step:
./deploy.sh build     # Build Docker image
./deploy.sh push      # Push to GCR
./deploy.sh deploy    # Deploy to Cloud Run

# Post-deployment:
./deploy.sh status    # Check service status
./deploy.sh health    # Verify health endpoint
./deploy.sh promote   # Route 100% traffic
```

---

## 📊 Testing

```bash
cd v1
python3 -m pytest tests/ -v
```

**Coverage:**
- Health endpoint validation
- Chat session management
- Lead capture with validation
- Error handling (404, 422)
- Integration flow
- Load testing

---

## 🔮 Migration to v2

Features from `archive/` that can be incrementally added:

| Feature | Source | Integration Point |
|---------|--------|-------------------|
| Multi-agent system | `archive/root_agent.py` | v1 chat endpoint |
| Firestore persistence | `archive/database/` | Replace in-memory storage |
| Appointment booking | `archive/appointment_manager.py` | Add `/appointments` endpoint |
| Full React frontend | `archive/frontend/` | Replace v1/frontend/ |
| Analytics | `archive/analytics_service.py` | Add `/analytics` endpoint |

---

## 📝 License

MIT License — use freely for any business.

---

Built with ❤️ by [Kadima Digital Strategies](https://github.com/arigatoexpress)
