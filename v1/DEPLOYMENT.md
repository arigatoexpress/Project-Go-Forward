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

4. **Setup Workload Identity Federation:**
   Follow the [GitHub Actions guide](https://github.com/google-github-actions/auth#setting-up-workload-identity-federation)

### Manual Deployment

If you prefer manual deployment:

```bash
export GOOGLE_CLOUD_PROJECT=my-project
./deploy.sh pipeline  # test → build → push → deploy
```

### Deployment Verification

After deployment, verify the service:

```bash
./deploy.sh status                           # Check service status
./deploy.sh health https://YOUR_URL.run.app  # Health check
./deploy.sh promote                          # Route 100% traffic
```

## Architecture

```
Git Push → GitHub Actions → Test → Build → Push to GCR → Deploy to Cloud Run
                ↓
         (no traffic initially)
                ↓
         Manual promotion to 100%
```

This allows safe testing before routing production traffic.
