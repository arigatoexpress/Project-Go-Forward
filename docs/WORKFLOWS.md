# THO App — Workflows

Diagrams are [Mermaid](https://mermaid.js.org/). GitHub renders them inline.

## 1. Lead → Customer → Deal → Funded

The core sales funnel.

```mermaid
flowchart LR
    A[Visitor on storefront] -->|Chat or contact form| B[Lead created]
    B -->|Qualification| C{Qualified?}
    C -->|No| D[Archived]
    C -->|Yes| E[Customer LEAD status]
    E -->|Appointment booked| F[Showing / tour]
    F -->|Selects home| G[Deal created: pending]
    G -->|Credit check| H{Credit OK?}
    H -->|Denied| I[Deal: denied]
    H -->|Approved| J[Deal: approved]
    J -->|Docs generated + signed| K[Deal: contract]
    K -->|Lender funds| L[Deal: funded]
    L -->|Delivery complete| M[Deal: complete]
    L -->|Notion Installation tracker starts| N[Installation: phase 1/11]
    M -->|Customer status| O[Customer: SOLD]
```

**Key transitions**:
- Customer status flips to `SOLD` when any associated Deal reaches `complete`.
- The Notion Installation Tracker picks up the Deal at `funded` and drives the 11-phase delivery process.

## 2. Document generation

What happens when a CRM user clicks "Generate" on a deal card.

```mermaid
sequenceDiagram
    participant U as Admin UI (CRM)
    participant API as FastAPI
    participant DB as Firestore (deals/)
    participant DE as Document Engine
    participant FM as field_map.json
    participant GCS as tho-secure-documents (GCS)
    participant C as Client browser

    U->>API: POST /api/deals/{id}/generate-document<br/>{template_name}
    API->>DB: get_deal(id)
    DB-->>API: Deal dict
    API->>DE: fill_pdf_form(template, Deal.to_document_data())
    DE->>FM: resolve field_map + checkbox_fields
    FM-->>DE: field mappings
    DE->>DE: XFA inject into datasets stream<br/>or AcroForm fallback
    DE->>GCS: upload generated PDF
    GCS-->>DE: public URL
    DE-->>API: {local_path, gcs_url}
    API-->>U: {download_url}
    U->>C: window.open(download_url)
```

**Template categories** (in `config/field_map.json`):
- **TMHA** — Texas Manufactured Housing Association sales contracts
- **TDHCA** — Texas Department of Housing & Community Affairs (20+ disclosure forms)
- **State** — state-level compliance (CreditAuth, PatriotAct, …)
- **Internal** — THO-specific (Homestead, Arbitration, CheckDraftAuth, …)

**Output location**: `gs://tho-secure-documents/generated_docs/{filename}` in production (`GCS_DOCUMENTS_BUCKET` env var). Local dev falls back to `./data/generated_docs/`.

## 3. Document packet (multi-template)

When a closing packet (10–20 documents) is needed:

```mermaid
flowchart TD
    A[POST /api/deals/id/generate-packet] --> B{Packet definition<br/>in field_map.json}
    B --> C[Resolve template list]
    C --> D[Loop: fill each template]
    D --> E{XFA success?}
    E -->|Yes| F[Write filled PDF]
    E -->|No| G[AcroForm fallback]
    G --> F
    F --> H[Merge all PDFs via pypdf]
    H --> I[Upload merged PDF to GCS]
    I --> J[Return packet URL]
```

## 4. AI-powered form extraction

Used when a sales rep has been chatting with a customer and wants to auto-populate Deal fields.

```mermaid
sequenceDiagram
    participant U as CRM user
    participant API as /api/documents/extract-fields
    participant Chat as Chat session store
    participant PII as pii_guard
    participant G as Gemini 2.0 Flash
    participant D as Deal record

    U->>API: POST {session_id, template_name}
    API->>Chat: Load transcript
    Chat-->>API: Messages
    API->>PII: Strip SSN, income, DOB, account numbers
    PII-->>API: Safe transcript
    API->>G: Prompt: extract these fields from conversation
    G-->>API: {extracted_data: {...}}
    API->>API: Validate extracted values against field definitions
    API-->>U: {extracted_data, message}
    U->>D: Review + apply to Deal
```

**Guardrail**: PII fields (`buyer_ssn`, `co_buyer_ssn`, financial account numbers) are **never** sent to the LLM. `form_extraction.py` filters them from both the transcript and the field list.

## 5. Admin auth

Stateless JWT via shared secret. No database lookup on the hot path.

```mermaid
sequenceDiagram
    participant User
    participant Frontend
    participant API
    participant Secret as ADMIN_PIN_HASH env
    
    User->>Frontend: Enter PIN
    Frontend->>API: POST /api/admin/verify {pin}
    API->>API: SHA256(pin)
    API->>Secret: Compare with ADMIN_PIN_HASH
    Secret-->>API: match
    API->>API: Generate HMAC-SHA256 JWT (8-byte exp + 16-byte sig)
    API-->>Frontend: {token, expires_at}
    Frontend->>Frontend: Store token in memory
    
    loop Every admin request
        Frontend->>API: request + X-Admin-Token header
        API->>API: _verify_admin_token(token)
        API-->>Frontend: authorized response
    end
```

**Token**: 24 bytes (8-byte BE uint64 expiry + 16-byte HMAC tag), base64 → ~32 chars. TTL default 2h (`ADMIN_TOKEN_TTL`). Secret derivation: `SHA256(f"sapphire-jwt-{ADMIN_PIN_HASH[:16]}")`.

## 6. Deployment (CI/CD)

```mermaid
flowchart LR
    A[Push to main] --> B[GitHub Actions]
    B --> C{Tests pass?}
    C -->|No| D[Fail build, notify]
    C -->|Yes| E[Docker build<br/>multi-stage]
    E --> F[Preflight:<br/>check_document_templates.py]
    F --> G{All 63 templates<br/>present?}
    G -->|No| D
    G -->|Yes| H[Auth via WIF<br/>no service account keys]
    H --> I[gcloud run deploy<br/>region us-central1]
    I --> J[Cloud Run health check]
    J --> K[Live on tho.sapphirealpha.xyz]
```

## 7. Inventory sync

External website (texashomeoutlet.com) → storefront.

```mermaid
flowchart LR
    A[texashomeoutlet.com] -->|scraper| B[tools/inventory_sync.py]
    B -->|normalize| C[InventoryItem dataclass]
    C -->|Firestore merge| D[inventory/ collection]
    D --> E[/api/inventory served by FastAPI/]
    E --> F[InventoryBrowse.jsx]
```

**Run**: `python tools/inventory_sync.py [--dry-run] [--force]`. Merges rather than overwrites, so manual edits in Firestore are preserved unless `--force` is set.

## 8. Cross-system integration — where Notion fits

This is the integration contour for Etai's Notion workspace. Details in [INTEGRATION_NOTION.md](INTEGRATION_NOTION.md).

```mermaid
flowchart TD
    subgraph THO[THO App / Firestore tho-ai-agent]
        A[Customer]
        B[Deal]
        C[Inventory]
        D[ServiceRequest]
    end
    subgraph Notion[Notion Workspace Etai building]
        E[Installation & Service Tracker<br/>11 phases + contractors]
        F[Warranty & Factory Billing<br/>AR tracker]
        G[Title]
        H[Insurance]
    end
    subgraph Drive[Google Drive Mark gates]
        I[Per-deal folder<br/>generated PDFs + scans]
    end
    
    B -->|deal_id reference| E
    B -->|deal_id reference| F
    B -->|deal_id reference| G
    B -->|deal_id reference| H
    D -->|service_request_id| E
    D -->|warranty flag| F
    B -->|generated PDFs| I
    I -->|link-back| E
    
    classDef src fill:#e1f5ff,stroke:#01579b
    classDef dest fill:#f0f4c3,stroke:#827717
    classDef bridge fill:#fce4ec,stroke:#880e4f
    class A,B,C,D src
    class E,F,G,H dest
    class I bridge
```

**Canonical rule**: the Deal lives in Firestore; Notion holds the operational workflow around it. IDs flow from THO app → Notion, never the reverse.


*Last verified: 2026-05-04*
