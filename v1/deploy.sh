#!/bin/bash
# Project Go Forward v1 - Unified Deployment Script
# Bridges local development and cloud deployment workflows
#
# Usage: ./deploy.sh [local|test|build|deploy|destroy|status]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
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

log_step() {
    echo -e "${CYAN}[STEP]${NC} $1"
}

# Configuration
SERVICE_NAME="${SERVICE_NAME:-project-go-forward-v1}"
REGION="${REGION:-us-central1}"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# Check prerequisites
check_prerequisites() {
    log_step "Checking prerequisites..."
    
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 is required but not installed"
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    log_info "Python version: $PYTHON_VERSION"
}

# Check gcloud
check_gcloud() {
    if ! command -v gcloud &> /dev/null; then
        log_error "gcloud CLI is required for cloud deployment"
        log_info "Install from: https://cloud.google.com/sdk/docs/install"
        exit 1
    fi
    
    # Check authentication
    if ! gcloud auth print-access-token &> /dev/null; then
        log_error "Not authenticated with gcloud"
        log_info "Run: gcloud auth login"
        exit 1
    fi
    
    # Get project ID if not set
    if [ -z "$PROJECT_ID" ]; then
        PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
        if [ -z "$PROJECT_ID" ]; then
            log_error "GOOGLE_CLOUD_PROJECT not set and no default project configured"
            exit 1
        fi
    fi
    
    log_info "GCP Project: $PROJECT_ID"
    log_info "Region: $REGION"
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
    log_step "Installing dependencies..."
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
    pip install -q pytest pytest-asyncio httpx
    log_success "Dependencies installed"
}

# Run tests
run_tests() {
    log_step "Running test suite..."
    python3 -m pytest tests/ -v --tb=short
    log_success "All tests passed"
}

# Run local development server
run_local() {
    log_step "Starting local development server..."
    export PORT="${PORT:-8080}"
    export HOST="${HOST:-0.0.0.0}"
    export DEBUG="true"
    
    log_info "Server will start on http://localhost:$PORT"
    log_info "API docs available at http://localhost:$PORT/docs"
    log_info "Health check at http://localhost:$PORT/health"
    echo "---"
    
    python3 -m app.main
}

# Build Docker image
build_image() {
    log_step "Building Docker image..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is required for building container images"
        exit 1
    fi
    
    local image_name="gcr.io/$PROJECT_ID/$SERVICE_NAME:$IMAGE_TAG"
    
    log_info "Building image: $image_name"
    docker build -t "$image_name" .
    
    log_success "Image built: $image_name"
    echo "$image_name" > .last_image_name
}

# Push Docker image to GCR
push_image() {
    log_step "Pushing image to Google Container Registry..."
    
    check_gcloud
    
    local image_name="gcr.io/$PROJECT_ID/$SERVICE_NAME:$IMAGE_TAG"
    
    # Configure docker to use gcloud credentials
    gcloud auth configure-docker --quiet 2>/dev/null || true
    
    log_info "Pushing: $image_name"
    docker push "$image_name"
    
    log_success "Image pushed to GCR"
}

# Deploy to Cloud Run
deploy_cloud_run() {
    log_step "Deploying to Google Cloud Run..."
    
    check_gcloud
    
    local image_name="gcr.io/$PROJECT_ID/$SERVICE_NAME:$IMAGE_TAG"
    
    log_info "Service: $SERVICE_NAME"
    log_info "Project: $PROJECT_ID"
    log_info "Region: $REGION"
    
    # Deploy with gcloud
    gcloud run deploy "$SERVICE_NAME" \
        --image "$image_name" \
        --region "$REGION" \
        --project "$PROJECT_ID" \
        --platform managed \
        --allow-unauthenticated \
        --memory 512Mi \
        --cpu 1 \
        --concurrency 80 \
        --max-instances 10 \
        --min-instances 0 \
        --timeout 300 \
        --port 8080 \
        --no-traffic 
    
    log_success "Deployment complete!"
    
    # Get service URL
    local service_url=$(gcloud run services describe "$SERVICE_NAME" \
        --region "$REGION" \
        --project "$PROJECT_ID" \
        --format 'value(status.url)' 2>/dev/null || echo "")
    
    if [ -n "$service_url" ]; then
        echo ""
        log_success "Service URL: $service_url"
        echo "$service_url" > .last_deploy_url
        
        # Show quick test commands
        echo ""
        log_info "Quick verification commands:"
        echo "  curl $service_url/health"
        echo "  curl $service_url/"
        echo ""
        log_info "To promote to 100% traffic:"
        echo "  ./deploy.sh promote"
    fi
}

# Promote deployment to 100% traffic
promote_traffic() {
    log_step "Promoting deployment to 100% traffic..."
    
    check_gcloud
    
    gcloud run services update-traffic "$SERVICE_NAME" \
        --to-latest \
        --region "$REGION" \
        --project "$PROJECT_ID"
    
    log_success "Traffic promoted to 100%"
}

# Check deployment status
show_status() {
    log_step "Checking deployment status..."
    
    check_gcloud
    
    echo ""
    log_info "Service Details:"
    gcloud run services describe "$SERVICE_NAME" \
        --region "$REGION" \
        --project "$PROJECT_ID" \
        --format 'table(
            status.conditions[0].type:label=STATUS,
            status.url:label=URL,
            spec.template.spec.containers[0].image:label=IMAGE
        )'
    
    echo ""
    log_info "Traffic Allocation:"
    gcloud run services describe "$SERVICE_NAME" \
        --region "$REGION" \
        --project "$PROJECT_ID" \
        --format 'value(status.traffic)' | tr ',' '\n' | while read line; do
        echo "  $line"
    done
}

# Destroy deployment
destroy_deployment() {
    log_step "Destroying Cloud Run deployment..."
    
    check_gcloud
    
    log_warn "This will delete the Cloud Run service: $SERVICE_NAME"
    read -p "Are you sure? (yes/no): " confirm
    
    if [ "$confirm" = "yes" ]; then
        gcloud run services delete "$SERVICE_NAME" \
            --region "$REGION" \
            --project "$PROJECT_ID" \
            --quiet
        log_success "Deployment destroyed"
    else
        log_info "Cancelled"
    fi
}

# Quick health check on deployed service
health_check() {
    local url="${1:-$(cat .last_deploy_url 2>/dev/null || echo "")}"
    
    if [ -z "$url" ]; then
        log_error "No URL provided and no previous deployment found"
        log_info "Usage: ./deploy.sh health <url>"
        exit 1
    fi
    
    log_step "Running health check on $url..."
    
    if command -v curl &> /dev/null; then
        local response=$(curl -s -w "\n%{http_code}" "$url/health" 2>/dev/null || echo "")
        local http_code=$(echo "$response" | tail -n1)
        local body=$(echo "$response" | sed '$d')
        
        if [ "$http_code" = "200" ]; then
            log_success "Health check passed"
            echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
        else
            log_error "Health check failed (HTTP $http_code)"
            echo "$body"
            exit 1
        fi
    else
        log_warn "curl not available, skipping health check"
    fi
}

# Full CI/CD pipeline
run_pipeline() {
    log_step "Running full CI/CD pipeline..."
    
    check_prerequisites
    setup_venv
    install_deps
    run_tests
    check_gcloud
    build_image
    push_image
    deploy_cloud_run
    
    log_success "Pipeline complete!"
}

# Show usage
show_usage() {
    cat << EOF
Project Go Forward v1 - Unified Deployment Script

Usage: $0 [COMMAND] [OPTIONS]

Commands:
    local           Run local development server (default)
    test            Run test suite
    setup           Setup virtual environment and install dependencies
    build           Build Docker image
    push            Push Docker image to GCR
    deploy          Deploy to Google Cloud Run
    promote         Promote deployment to 100% traffic
    pipeline        Run full CI/CD pipeline (test → build → deploy)
    status          Check deployment status
    health [url]    Run health check on deployed service
    destroy         Destroy Cloud Run deployment
    help            Show this help message

Environment Variables:
    GOOGLE_CLOUD_PROJECT    GCP project ID (required for cloud ops)
    REGION                  GCP region (default: us-central1)
    SERVICE_NAME            Cloud Run service name (default: project-go-forward-v1)
    IMAGE_TAG               Docker image tag (default: latest)
    PORT                    Local server port (default: 8080)

Examples:
    # Local development
    $0 local
    
    # Run tests
    $0 test
    
    # Full deployment pipeline
    GOOGLE_CLOUD_PROJECT=my-project $0 pipeline
    
    # Deploy only (skip tests)
    GOOGLE_CLOUD_PROJECT=my-project $0 deploy
    
    # Check status
    $0 status
    
    # Health check
    $0 health https://your-service-url.run.app

Workflow:
    1. Develop locally:        ./deploy.sh local
    2. Run tests:              ./deploy.sh test
    3. Deploy to Cloud Run:    GOOGLE_CLOUD_PROJECT=my-project ./deploy.sh pipeline
    4. Verify deployment:      ./deploy.sh health
    5. Promote traffic:        ./deploy.sh promote

EOF
}

# Main command dispatcher
main() {
    case "${1:-local}" in
        local)
            check_prerequisites
            setup_venv
            install_deps
            run_local
            ;;
        test)
            check_prerequisites
            setup_venv
            install_deps
            run_tests
            ;;
        setup)
            check_prerequisites
            setup_venv
            install_deps
            log_success "Setup complete! Run './deploy.sh local' to start the server."
            ;;
        build)
            check_gcloud
            build_image
            ;;
        push)
            check_gcloud
            push_image
            ;;
        deploy)
            check_gcloud
            deploy_cloud_run
            ;;
        promote)
            promote_traffic
            ;;
        pipeline)
            run_pipeline
            ;;
        status)
            show_status
            ;;
        health)
            health_check "$2"
            ;;
        destroy)
            destroy_deployment
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
