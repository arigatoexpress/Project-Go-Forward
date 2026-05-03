# DocuSeal sidecar service
# Pulls the official DocuSeal image and wires up the env vars required for
# Cloud Run deployment. NOT deployed — activation requires Ari's sign-off.
#
# To run locally for testing:
#   docker run -it --rm -p 3000:3000 \
#     -e SECRET_KEY_BASE=$(openssl rand -hex 32) \
#     -v $(pwd)/docuseal_data:/data \
#     docuseal/docuseal
#
# Production env vars are injected via Cloud Run secrets (see cloudbuild.yaml).

FROM docuseal/docuseal:latest

# Cloud Run requires the container to listen on $PORT (default 8080).
# DocuSeal respects PORT natively since v1.5.
ENV PORT=8080

# Persist SQLite DB on a Cloud Run volume mount at /data.
# Switch to DATABASE_URL=postgres://... when migrating to Cloud SQL.
ENV DATABASE_URL=sqlite3:///data/docuseal.sqlite3

# Force production mode
ENV RAILS_ENV=production
ENV RACK_ENV=production

EXPOSE 8080
