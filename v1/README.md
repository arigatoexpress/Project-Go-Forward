# Project Go Forward v1

A deployable core product slice of the THO (Project Go Forward) business clone.

## Overview

This is a minimal, tested vertical slice featuring:
- **FastAPI** backend with health checks and structured endpoints
- **Simple chat** endpoint with session management
- **Lead capture** functionality for business inquiries
- **Comprehensive test suite** (20 tests, all passing)
- **Docker** containerization ready
- **Cloud Run** deployment with unified script
- **Reproducible workflows** for local and cloud environments

## Quick Start

```bash
# Setup environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Run locally
uvicorn app.main:app --reload --port 8080

# Or use the deploy script
cd .. && ./deploy.sh local
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API info and available endpoints |
| `/health` | GET | Health check with version and uptime |
| `/chat` | POST | Send message, get AI-like response |
| `/leads` | POST | Capture lead information |
| `/leads` | GET | List captured leads |
| `/sessions/{id}` | GET | Get conversation history |
| `/docs` | GET | OpenAPI/Swagger documentation |

### Example API Usage

```bash
# Health check
curl http://localhost:8080/health

# Start a conversation
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, I'm interested in pricing"}'

# Capture a lead
curl -X POST http://localhost:8080/leads \
  -H "Content-Type: application/json" \
  -d '{"name": "John Doe", "phone": "555-123-4567", "interest": "3 bedroom home"}'
```

## Project Structure

```
v1/
├── app/
│   ├── __init__.py
│   └── main.py          # FastAPI application
├── tests/
│   ├── __init__.py
│   └── test_main.py     # 20 tests covering all endpoints
├── .github/workflows/
│   ├── test.yml         # PR testing workflow
│   └── deploy.yml       # Full CI/CD pipeline
├── Dockerfile           # Container definition
├── requirements.txt     # Python dependencies
└── README.md           # This file
```

## CI/CD Workflows

### Pull Request Testing
Automatically runs tests on every PR to main/master.

### Deploy Pipeline
On push to main/master:
1. Run test suite
2. Build Docker image
3. Deploy to Cloud Run (no-traffic)
4. Run health check
5. Output deployment URL

See [DEPLOYMENT.md](DEPLOYMENT.md) for setup instructions.

## Deployment

### Local Development
```bash
./deploy.sh local
```

### Cloud Run (via script)
```bash
export GCP_PROJECT_ID=your-project-id
./deploy.sh cloud
```

### Cloud Run (via GitHub Actions)
Configure secrets in GitHub repository settings:
- `GCP_PROJECT_ID`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT_EMAIL`

Push to main branch triggers automatic deployment.

## License

Internal use only - THO Business Clone project.
