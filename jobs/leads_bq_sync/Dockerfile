# tho-leads-bq: daily PII-free Firestore -> BigQuery (tho_analytics) sync.
# Small, job-only image: it contains the one sync script and its two client
# libraries, not the storefront app. Build from the repo root:
#   gcloud builds submit --project tho-ai-agent --config jobs/leads_bq_sync/cloudbuild.yaml .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY jobs/leads_bq_sync/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY scripts/sync_leads_to_bigquery.py /app/sync_leads_to_bigquery.py

RUN useradd --system --uid 10001 --home-dir /app job
USER job

ENTRYPOINT ["python", "/app/sync_leads_to_bigquery.py"]
