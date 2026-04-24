# Inventory Integration Guide — SUPERSEDED

This file is kept as a stub for backlinks. The content here was outdated and misleading (it claimed Firestore lived in `sapphire-479610`; it actually lives in `tho-ai-agent`).

Current integration documentation lives in [docs/](docs/).

- Inventory model and API: [docs/DATA_MODEL.md](docs/DATA_MODEL.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- External integration contract (Notion, n8n, partners): [docs/INTEGRATION_NOTION.md](docs/INTEGRATION_NOTION.md)
- Security + principle of least privilege: [docs/SECURITY.md](docs/SECURITY.md)

To sync inventory into Firestore, run the existing tooling:

```bash
python tools/inventory_sync.py [--dry-run] [--force]
```

For writing inventory from another service, use the authenticated `/api/v1/inventory` endpoints (shipping soon — see INTEGRATION_NOTION.md §4) rather than writing to Firestore directly across projects.
