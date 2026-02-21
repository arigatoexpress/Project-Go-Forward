# Legacy Business Clone - Archived

**Archive Date:** 2026-02-21  
**Reason:** Consolidated into v1 deployable vertical slice

## Contents

This directory contains the legacy full business clone codebase that has been archived in favor of the v1 minimal vertical slice.

### Legacy Components

| File/Directory | Description | Lines of Code |
|----------------|-------------|---------------|
| `main.py` | Full FastAPI application with all features | ~868 |
| `root_agent.py` | Agent orchestration logic | ~300 |
| `appointment_manager.py` | Appointment scheduling system | ~280 |
| `lead_management.py` | Lead capture and tracking | ~200 |
| `analytics_service.py` | Analytics and reporting | ~60 |
| `conversation_memory.py` | Conversation history management | ~320 |
| `caching.py` | Redis/caching layer | ~110 |
| `structured_logging.py` | Logging infrastructure | ~140 |
| `config_loader.py` | Configuration management | ~70 |
| `config/` | Configuration files | - |
| `database/` | Database models and migrations | - |
| `data/` | Sample data files | - |
| `schemas/` | Pydantic schemas | - |
| `tools/` | Tool integrations | - |
| `tests/` | Legacy test suite | - |

### Why Archived

The legacy codebase was fully functional but overly complex for the initial deployment. The v1 vertical slice:

1. **Focuses on core features:** Chat, leads, health checks
2. **Is fully tested:** 20 tests, all passing
3. **Has clear deployment path:** Unified deploy.sh script
4. **Is containerized:** Docker ready
5. **Deploys to Cloud Run:** Production-ready configuration

### Migration Notes

Key features from legacy that can be incrementally added to v1:

- **Appointment scheduling** → Add `/appointments` endpoint
- **Full analytics** → Add `/analytics` endpoint with persistence
- **Conversation memory with Firestore** → Replace in-memory storage
- **Multi-agent orchestration** → Integrate Google ADK
- **Redis caching** → Add caching middleware
- **Authentication** → Add JWT/auth middleware

### Restoration

If needed, components can be extracted from this archive and integrated into the v1 architecture following the patterns established in:
- `v1/app/main.py` - Clean endpoint structure
- `v1/tests/test_main.py` - Test patterns
- `v1/deploy.sh` - Deployment workflow

---

*This archive is preserved for reference and potential future integration.*
