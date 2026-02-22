# Legacy Business Clone - Archived

**Archive Date:** 2026-02-21  
**Merge Date:** 2026-02-21  
**Reason:** Consolidated into v1 deployable vertical slice

## Merge Notes

This archive was updated with unmerged changes from the legacy business clone at:
`/Users/aribs/Documents/Business/Kadima Digital Strategies 2026/THO_MASTER/project-go-forward`

### Merged Commits (from legacy/main)

| Commit | Description |
|--------|-------------|
| `3f2afd7` | Polish frontend: unified nav, design tokens, loading skeletons, mobile UX |
| `b606ac9` | Add email service (Resend) and CRM Dashboard |
| `2c50549` | Add sort, search, lead capture, and mobile UX to Inventory Browse |
| `72860d3` | Sync inventory across browse page and chat agent, add 12 missing homes |
| `a62c106` | Add Inventory Browse page as new homepage with photo galleries and 3D tours |
| `1ca2e5d` | Expand photo galleries: 185 real photos across 15 homes with room categories |
| `c371829` | Ad Studio overhaul: real property photos, Matterport 3D tours, anti-slop quality scoring |
| `a020963` | Phase 4: Deal management, full PDF mapping, FCD data import, agent personality |
| `582caab` | Phase 2-3: Document engine, security hardening, UI enhancements, real inventory |

### Key New Components

- **email_service.py** - Resend email integration
- **tools/document_engine.py** - PDF document processing
- **tools/crm_tools.py** - CRM integration tools
- **frontend/src/pages/CRM.jsx** - CRM Dashboard UI
- **frontend/src/pages/InventoryBrowse.jsx** - Inventory browsing with search/filters
- **scripts/import_fcd_deals.py** - FCD data import script

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
