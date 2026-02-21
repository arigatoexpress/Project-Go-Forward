#!/bin/bash
# Project Go Forward v1 - Local Development Runner
# Usage: ./run.sh [local|docker|test]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check Python version
check_python() {
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 is not installed"
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    log_info "Python version: $PYTHON_VERSION"
}

# Setup virtual environment
setup_venv() {
    if [ ! -d ".venv" ]; then
        log_info "Creating virtual environment..."
        python3 -m venv .venv
    fi
    
    source .venv/bin/activate
    log_success "Virtual environment activated"
}

# Install dependencies
install_deps() {
    log_info "Installing dependencies..."
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
    pip install -q pytest pytest-asyncio httpx
    log_success "Dependencies installed"
}

# Run tests
run_tests() {
    log_info "Running tests..."
    python -m pytest tests/ -v --tb=short
    log_success "Tests completed"
}

# Run local server
run_local() {
    log_info "Starting local development server..."
    export PORT="${PORT:-8080}"
    export HOST="${HOST:-0.0.0.0}"
    export DEBUG="true"
    
    log_info "Server will start on http://localhost:$PORT"
    log_info "API docs available at http://localhost:$PORT/docs"
    echo "---"
    
    python -m app.main
}

# Run with Docker
run_docker() {
    log_info "Building and running with Docker..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        exit 1
    fi
    
    docker build -t project-go-forward:v1 .
    docker run -p 8080:8080 --env-file .env project-go-forward:v1
}

# Deploy to Cloud Run
run_deploy() {
    log_info "Deploying to Google Cloud Run..."
    
    if ! command -v gcloud &> /dev/null; then
        log_error "gcloud CLI is not installed"
        exit 1
    fi
    
    PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
    
    if [ -z "$PROJECT_ID" ]; then
        log_error "GOOGLE_CLOUD_PROJECT not set and no default project configured"
        exit 1
    fi
    
    log_info "Deploying to project: $PROJECT_ID"
    
    gcloud run deploy project-go-forward-v1 \
        --source . \
        --region us-central1 \
        --project "$PROJECT_ID" \
        --allow-unauthenticated \
        --memory 512Mi \
        --max-instances 5
    
    log_success "Deployment complete"
}

# Show usage
show_usage() {
    cat << EOF
Project Go Forward v1 - Runner Script

Usage: $0 [COMMAND]

Commands:
    local       Run local development server (default)
    test        Run test suite
    docker      Build and run with Docker
    deploy      Deploy to Google Cloud Run
    setup       Setup virtual environment and install dependencies
    check       Run health check on running server

Environment Variables:
    PORT        Server port (default: 8080)
    HOST        Server host (default: 0.0.0.0)
    DEBUG       Enable debug mode (default: false)
    GOOGLE_CLOUD_PROJECT    GCP project ID for deploy

Examples:
    $0 local        # Start development server
    $0 test         # Run tests
    $0 docker       # Run in Docker container
    $0 deploy       # Deploy to Cloud Run
EOF
}

# Run health check
run_check() {
    local url="${1:-http://localhost:8080}"
    log_info "Checking health at $url/health..."
    
    if command -v curl &> /dev/null; then
        curl -s "$url/health" | python3 -m json.tool || echo "Raw response: $(curl -s "$url/health")"
    else
        log_warn "curl not available, skipping health check"
    fi
}

# Main command dispatcher
main() {
    case "${1:-local}" in
        local)
            check_python
            setup_venv
            install_deps
            run_local
            ;;
        test)
            check_python
            setup_venv
            install_deps
            run_tests
            ;;
        docker)
            run_docker
            ;;
        deploy)
            run_deploy
            ;;
        setup)
            check_python
            setup_venv
            install_deps
            log_success "Setup complete! Run './run.sh local' to start the server."
            ;;
        check)
            run_check "$2"
            ;;
        help|--help|-h)
            show_usage
            ;;
        *)
            log_error "Unknown command: $1"
            show_usage
            exit 1
            ;;
    esac
}

main "$@"
