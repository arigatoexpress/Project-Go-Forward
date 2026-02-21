# Project Go Forward v1

A deployable core product slice of the THO (Project Go Forward) business clone.

## Overview

This is a minimal, tested vertical slice featuring:
- FastAPI backend with health checks
- Simple chat endpoint with session management
- Lead capture functionality
- Comprehensive test suite
- Docker and Cloud Run deployment ready

## Quick Start

```bash
# Setup
./run.sh setup

# Run locally
./run.sh local

# Run tests
./run.sh test

# Deploy to Cloud Run
./run.sh deploy
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API info |
| `/health` | GET | Health check |
| `/chat` | POST | Send message, get response |
| `/leads` | POST | Capture lead |
| `/leads` | GET | List leads |
| `/sessions/{id}` | GET | Get session history |
| `/docs` | GET | OpenAPI docs |

## Project Structure

```
v1/
├── app/
│   ├── __init__.py
│   └── main.py          # FastAPI application
├── tests/
│   └── test_main.py     # Test suite
├── requirements.txt     # Python dependencies
├── Dockerfile           # Container image
├── run.sh              # Development/deployment script
├── pytest.ini          # Test configuration
└── README.md           # This file
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 8080 | Server port |
| `HOST` | 0.0.0.0 | Server host |
| `DEBUG` | false | Enable debug mode |

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=term-missing
```

## Deployment

### Local Docker

```bash
docker build -t project-go-forward:v1 .
docker run -p 8080:8080 project-go-forward:v1
```

### Google Cloud Run

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
./run.sh deploy
```

## Architecture

This v1 slice demonstrates:
- Clean API design with Pydantic models
- Session-based conversation tracking
- In-memory storage (replace with Firestore in production)
- Structured logging preparation
- Health check pattern for load balancers
- Security headers middleware

## Next Steps (v2)

- Integrate Google ADK for intelligent responses
- Add Firestore database persistence
- Implement full inventory search
- Add authentication/authorization
- WebSocket support for real-time chat
