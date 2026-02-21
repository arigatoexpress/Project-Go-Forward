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
./deploy.sh setup

# Run tests
./deploy.sh test

# Run locally
./deploy.sh local

# Deploy to Cloud Run (requires GOOGLE_CLOUD_PROJECT)
GOOGLE_CLOUD_PROJECT=my-project ./deploy.sh pipeline
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
  -d '{"message": "Hello, I'm interested in your services"}'

# Capture a lead
curl -X POST http://localhost:8080/leads \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John Doe",
    "email": "john@example.com",
    "phone": "555-1234",
    "interest": "Product inquiry about pricing"
  }'
```

## Project Structure

```
v1/
├── app/
│   ├── __init__.py
│   └── main.py              # FastAPI application with all endpoints
├── tests/
│   └── test_main.py         # Comprehensive test suite (20 tests)
├── requirements.txt         # Python dependencies
├── Dockerfile               # Container image definition
├── deploy.sh               # Unified deployment script ⭐
├── run.sh                  # Legacy development runner
├── pytest.ini              # Test configuration
├── .gcloudignore           # Files excluded from cloud builds
└── README.md               # This file
```

## Deployment Commands

### Local Development

```bash
# Setup and run
./deploy.sh setup    # Create venv, install deps
./deploy.sh local    # Start development server
./deploy.sh test     # Run test suite
```

### Cloud Deployment

```bash
# Full CI/CD pipeline (test → build → deploy)
GOOGLE_CLOUD_PROJECT=my-project ./deploy.sh pipeline

# Individual steps
GOOGLE_CLOUD_PROJECT=my-project ./deploy.sh build    # Build Docker image
GOOGLE_CLOUD_PROJECT=my-project ./deploy.sh push     # Push to GCR
GOOGLE_CLOUD_PROJECT=my-project ./deploy.sh deploy   # Deploy to Cloud Run

# Post-deployment
./deploy.sh status                           # Check deployment status
./deploy.sh health https://your-url.run.app  # Health check
./deploy.sh promote                          # Promote to 100% traffic
./deploy.sh destroy                          # Remove deployment
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CLOUD_PROJECT` | - | **Required** for cloud deployment |
| `REGION` | us-central1 | GCP region |
| `SERVICE_NAME` | project-go-forward-v1 | Cloud Run service name |
| `IMAGE_TAG` | latest | Docker image tag |
| `PORT` | 8080 | Local server port |
| `DEBUG` | false | Enable debug mode |

## Testing

```bash
# Run all tests
./deploy.sh test

# Or with pytest directly
python3 -m pytest tests/ -v

# Run with coverage
python3 -m pytest tests/ --cov=app --cov-report=term-missing
```

### Test Coverage

- ✅ Health endpoint validation
- ✅ Root endpoint API info
- ✅ Chat session creation and management
- ✅ Lead capture with validation
- ✅ Session history retrieval
- ✅ Error handling (404, 422)
- ✅ Full integration flow
- ✅ Load testing

## Architecture

```
┌─────────────────────────────────────────────┐
│  Client (Web/Mobile)                        │
└──────────────┬──────────────────────────────┘
               │ HTTP/JSON
┌──────────────▼──────────────────────────────┐
│  Cloud Run / Local                          │
│  ┌─────────────────────────────────────┐    │
│  │  FastAPI Application                │    │
│  │  ┌─────────┐  ┌─────────┐          │    │
│  │  │  Chat   │  │  Leads  │          │    │
│  │  │Endpoint │  │Endpoint │          │    │
│  │  └────┬────┘  └────┬────┘          │    │
│  │       └─────────────┘               │    │
│  │              │                      │    │
│  │  ┌───────────▼────────────┐         │    │
│  │  │  In-Memory Storage     │         │    │
│  │  │  (sessions, leads)     │         │    │
│  │  └────────────────────────┘         │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

This v1 slice demonstrates:
- Clean API design with Pydantic models
- Session-based conversation tracking
- In-memory storage (replace with Firestore in production)
- Structured logging preparation
- Health check pattern for load balancers
- Security headers middleware
- Input validation and error handling

## Docker

```bash
# Build locally
docker build -t project-go-forward:v1 .

# Run locally
docker run -p 8080:8080 project-go-forward:v1

# Build for GCP
docker build -t gcr.io/PROJECT/project-go-forward-v1:latest .
docker push gcr.io/PROJECT/project-go-forward-v1:latest
```

## Cloud Run Deployment Details

The deployment uses these configurations:
- **Memory**: 512Mi (adjustable)
- **CPU**: 1
- **Concurrency**: 80 requests per instance
- **Min Instances**: 0 (scales to zero)
- **Max Instances**: 10
- **Timeout**: 300 seconds
- **Port**: 8080

Traffic is initially deployed with `--no-traffic` for safe testing. Use `./deploy.sh promote` to shift 100% traffic after verification.

## Migration Path to v2

This v1 slice is designed for:
1. ✅ **Immediate deployment** - Get a working product live
2. ✅ **Testing infrastructure** - Validate CI/CD pipelines
3. ✅ **Stakeholder demos** - Show tangible progress
4. 🔄 **Foundation for v2** - Gradual enhancement

### v2 Enhancements (Future)

- Integrate Google ADK for intelligent responses
- Add Firestore database persistence
- Implement full inventory search
- Add authentication/authorization
- WebSocket support for real-time chat
- React frontend integration
- Multi-agent orchestration

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup GCP Auth
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: 'projects/PROJECT/locations/global/workloadIdentityPools/POOL/providers/PROVIDER'
          service_account: 'deployer@PROJECT.iam.gserviceaccount.com'
      - name: Deploy
        working-directory: ./v1
        run: |
          gcloud config set project $GOOGLE_CLOUD_PROJECT
          ./deploy.sh pipeline
```

## Monitoring

After deployment, monitor via:
- **Cloud Run Console**: https://console.cloud.google.com/run
- **Logs**: `gcloud logging read "resource.type=cloud_run_revision"`
- **Metrics**: CPU, memory, request latency in Cloud Console

## License

MIT License — use freely for any business.

---

Built with ❤️ by [Kadima Digital Strategies](https://github.com/arigatoexpress)
