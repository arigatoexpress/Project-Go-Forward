# Deployment Guide

## Local Development

```bash
./deploy.sh setup    # Create venv, install deps
./deploy.sh test     # Run tests
./deploy.sh local    # Start dev server on :8080
```

## GitHub Actions CI/CD

The repository includes automated CI/CD workflows:

### Workflows

- **`test.yml`** - Runs on every PR to main/master
- **`deploy.yml`** - Runs on push to main/master (test → build → deploy)

### Required GitHub Secrets

Configure these in your GitHub repository settings:

| Secret | Description | Example |
|--------|-------------|---------|
| `GCP_PROJECT_ID` | Google Cloud Project ID | `my-project-123` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Workload Identity Provider | `projects/123/locations/global/workloadIdentityPools/pool/providers/provider` |
| `GCP_SERVICE_ACCOUNT_EMAIL` | Service Account for deployments | `deployer@my-project.iam.gserviceaccount.com` |

### GCP Setup

1. **Enable APIs:**
   ```bash
   gcloud services enable run.googleapis.com
   gcloud services enable containerregistry.googleapis.com
   ```

2. **Create Service Account:**
   ```bash
   gcloud iam service-accounts create deployer \
     --display-name="GitHub Actions Deployer"
   ```

3. **Grant Permissions:**
   ```bash
   gcloud projects add-iam-policy-binding PROJECT_ID \
     --member="serviceAccount:deployer@PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/run.admin"
   
   gcloud projects add-iam-policy-binding PROJECT_ID \
     --member="serviceAccount:deployer@PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/storage.admin"
   ```

4. **Configure Workload Identity Federation:**
   Follow the [Google GitHub Actions documentation](https://github.com/google-github-actions/auth#setup) to configure Workload Identity Federation for secure, keyless authentication.

## Manual Cloud Run Deployment

If you prefer not to use GitHub Actions:

```bash
cd v1/

# Build
gcloud builds submit --tag gcr.io/PROJECT_ID/project-go-forward-v1

# Deploy
gcloud run deploy project-go-forward-v1 \
  --image gcr.io/PROJECT_ID/project-go-forward-v1 \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated
```

## Monitoring

After deployment, monitor your service:

```bash
# View logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=project-go-forward-v1" --limit=50

# Check service status
gcloud run services describe project-go-forward-v1 --region us-central1
```
