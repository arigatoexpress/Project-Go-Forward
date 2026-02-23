#!/bin/bash
# Deploy Script for THO (Project Go Forward)
# Usage: ./deploy.sh [local|cloud|test|build]
#
# This script deploys the v1 vertical slice - a minimal, tested, deployable
# core product with FastAPI, tests, and Cloud Run deployment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/v1"  # Point to v1 vertical slice
GCP_PROJECT_ID="${GCP_PROJECT_ID:-tho-ai-agent}"
GCP_REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-project-go-forward-v1}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v python3 &> /dev/null; then
        log_error "python3 is required but not installed"
        exit 1
    fi
    
    if [[ "${1:-}" == "cloud" ]]; then
        if ! command -v gcloud &> /dev/null; then
            log_error "gcloud CLI is required for cloud deployment"
            exit 1
        fi
        
        if ! gcloud config get-value project &> /dev/null; then
            log_warn "No GCP project configured. Set with: gcloud config set project YOUR_PROJECT_ID"
        fi
    fi
    
    log_info "Prerequisites OK"
}

# Setup virtual environment
setup_venv() {
    log_info "Setting up virtual environment..."
    
    cd "$PROJECT_DIR"
    
    if [[ ! -d ".venv" ]]; then
        python3 -m venv .venv
        log_info "Created virtual environment"
    fi
    
    # Install/upgrade pip and dependencies
    .venv/bin/python -m pip install --upgrade pip -q
    .venv/bin/python -m pip install -r requirements.txt -q
    
    log_info "Virtual environment ready"
}

# Run tests
run_tests() {
    log_info "Running tests..."
    
    cd "$PROJECT_DIR"
    
    # Install pytest if not present
    .venv/bin/python -m pip install pytest pytest-asyncio httpx -q 2>/dev/null || true
    
    # Run pytest with the venv Python
    if .venv/bin/python -m pytest tests/ -v --tb=short 2>&1; then
        log_info "All tests passed!"
    else
        log_error "Some tests failed - check output above"
        exit 1
    fi
}

# Build frontend (placeholder for v1 - no frontend in vertical slice)
build_frontend() {
    log_info "Skipping frontend build (v1 vertical slice has no frontend)"
}

# Deploy locally
deploy_local() {
    log_info "Starting local deployment..."
    
    cd "$PROJECT_DIR"
    
    setup_venv
    run_tests
    build_frontend
    
    log_info "Starting local server on http://localhost:8080"
    log_info "API docs available at http://localhost:8080/docs"
    log_info "Press Ctrl+C to stop"
    
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
}

# Deploy to Cloud Run
deploy_cloud() {
    log_info "Starting cloud deployment to Cloud Run..."
    
    cd "$PROJECT_DIR"
    
    setup_venv
    run_tests
    build_frontend
    
    log_info "Deploying to Cloud Run..."
    log_info "Project: $GCP_PROJECT_ID"
    log_info "Region: $GCP_REGION"
    log_info "Service: $SERVICE_NAME"
    
    gcloud run deploy "$SERVICE_NAME" \
        --source . \
        --region "$GCP_REGION" \
        --project "$GCP_PROJECT_ID" \
        --allow-unauthenticated \
        --memory 512Mi \
        --cpu 1 \
        --concurrency 80 \
        --max-instances 10 \
        --min-instances 0 \
        --timeout 300 \
        --port 8080
    
    log_info "Deployment complete!"
    
    # Get the service URL
    SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
        --region "$GCP_REGION" \
        --project "$GCP_PROJECT_ID" \
        --format 'value(status.url)')
    
    log_info "Service URL: $SERVICE_URL"
    log_info "Health check: $SERVICE_URL/health"
}

# Main execution
main() {
    local deploy_target="${1:-local}"
    
    log_info "THO Project Go Forward - Deploy Script"
    log_info "Target: $deploy_target"
    
    check_prerequisites "$deploy_target"
    
    case "$deploy_target" in
        local)
            deploy_local
            ;;
        cloud)
            deploy_cloud
            ;;
        test)
            setup_venv
            run_tests
            ;;
        build)
            setup_venv
            build_frontend
            log_info "Build complete - ready for deployment"
            ;;
        *)
            echo "Usage: $0 [local|cloud|test|build]"
            echo ""
            echo "Commands:"
            echo "  local  - Run locally with venv setup and tests"
            echo "  cloud  - Deploy to Google Cloud Run"
            echo "  test   - Run test suite only"
            echo "  build  - Build frontend and prepare for deployment"
            exit 1
            ;;
    esac
}

main "$@"
