# Deploying vibeshub to Azure

> Prefer clicking? Use [README-portal.md](README-portal.md) for an Azure Portal walkthrough that covers the same steps with no CLI. Env var template: [`./.env.example`](./.env.example).

The backend container in [./Dockerfile](./Dockerfile) is the only deployable artifact — it serves the built frontend out of `frontend_dist/`. The recommended Azure topology:

| Component | Azure service |
|---|---|
| App container | Azure Container Apps (or App Service for Containers) |
| Image registry | Azure Container Registry (ACR) |
| Database | Azure Database for PostgreSQL — Flexible Server |
| Trace blobs | Azure Storage Account + Blob container |
| Identity | User-assigned managed identity, granted **Storage Blob Data Contributor** on the storage account |

## 1. Provision infrastructure

Replace the placeholder values; the example uses `eastus` and resource group `vibeshub-rg`.

```bash
RG=vibeshub-rg
LOC=eastus
ACR=vibeshubacr$RANDOM            # must be globally unique
STORAGE=vibeshubstor$RANDOM       # must be globally unique, lowercase
PG=vibeshub-pg-$RANDOM            # must be globally unique
PG_ADMIN=vibeshub
PG_PASSWORD='<strong-password>'
DB_NAME=vibeshub
CONTAINER=traces
APP_ENV=vibeshub-env
APP=vibeshub
MI=vibeshub-mi

az group create -n $RG -l $LOC

# Container registry (admin disabled — managed identity will pull)
az acr create -n $ACR -g $RG --sku Basic

# Postgres flexible server + DB
az postgres flexible-server create \
  -g $RG -n $PG -l $LOC \
  --admin-user $PG_ADMIN --admin-password "$PG_PASSWORD" \
  --tier Burstable --sku-name Standard_B1ms --version 16 \
  --storage-size 32 --public-access 0.0.0.0
az postgres flexible-server db create -g $RG -s $PG -d $DB_NAME

# Storage account + blob container for trace bodies
az storage account create -n $STORAGE -g $RG -l $LOC --sku Standard_LRS --kind StorageV2
az storage container create --account-name $STORAGE -n $CONTAINER --auth-mode login
# If this fails with AuthorizationPermissionMismatch, grant yourself data-plane
# access first (control-plane Owner is not enough), then retry:
#   ME=$(az ad signed-in-user show --query id -o tsv)
#   az role assignment create --assignee-object-id $ME --assignee-principal-type User \
#     --role "Storage Blob Data Contributor" --scope $(az storage account show -n $STORAGE -g $RG --query id -o tsv)

# User-assigned managed identity
MI_ID=$(az identity create -g $RG -n $MI --query id -o tsv)
MI_CLIENT_ID=$(az identity show -g $RG -n $MI --query clientId -o tsv)
MI_PRINCIPAL_ID=$(az identity show -g $RG -n $MI --query principalId -o tsv)

# Grant the MI access to the storage account
STORAGE_ID=$(az storage account show -n $STORAGE -g $RG --query id -o tsv)
az role assignment create \
  --assignee-object-id $MI_PRINCIPAL_ID --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope $STORAGE_ID

# Grant the MI pull access to the registry (Container Apps pulls with this identity)
ACR_ID=$(az acr show -n $ACR -g $RG --query id -o tsv)
az role assignment create \
  --assignee-object-id $MI_PRINCIPAL_ID --assignee-principal-type ServicePrincipal \
  --role "AcrPull" --scope $ACR_ID

# Container Apps environment
az containerapp env create -n $APP_ENV -g $RG -l $LOC
```

## 2. Build and push the image

The Dockerfile expects the built frontend under `webapp/backend/frontend_dist/`, so build the SPA first.

```bash
(cd webapp/frontend && npm install && npm run build:deploy)

az acr login -n $ACR
docker buildx build --platform linux/amd64 \
  -t $ACR.azurecr.io/vibeshub:latest \
  --file deploy/azure/Dockerfile \
  --push webapp/backend
```

`--platform linux/amd64` is required on Apple Silicon (or any arm64 host) — Container Apps rejects arm64 images with *"Selected tag uses an invalid architecture 'arm64'."* `buildx ... --push` builds and pushes in one step, so no separate `docker push` is needed.

If you'd rather not run Docker locally, use `az acr build -r $ACR -t vibeshub:latest --file deploy/azure/Dockerfile webapp/backend` — ACR builds on amd64 by default. The provided [./deploy.sh](./deploy.sh) wraps steps 2 and 3 (build the SPA, `az acr build`, create-or-update the Container App) once the infrastructure from step 1 exists. Before running it, copy [`./.env.example`](./.env.example) to `deploy/azure/.env` and fill it in, then edit the `RG` / `ACR` / `APP` variables at the top of the script to match the names you chose above (they default to `vibeshub`).

## 3. Deploy the container app

```bash
PG_HOST=$(az postgres flexible-server show -g $RG -n $PG --query fullyQualifiedDomainName -o tsv)
DATABASE_URL="postgresql+psycopg://${PG_ADMIN}:${PG_PASSWORD}@${PG_HOST}:5432/${DB_NAME}?sslmode=require"
ACCOUNT_URL="https://${STORAGE}.blob.core.windows.net"
PUBLIC_URL="https://${APP}.<region-suffix>.azurecontainerapps.io"   # fill in after first deploy

SESSION_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')
TOKEN_ENCRYPTION_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
# The Fernet command needs the `cryptography` package: use the repo venv
# (./env/bin/python) or `pip install cryptography` first. On macOS use python3.
OAUTH_CLIENT_ID='<github-oauth-client-id>'
OAUTH_CLIENT_SECRET='<github-oauth-client-secret>'
GITHUB_FALLBACK_TOKEN='<server-pat>'   # any token; no scopes needed for public data

az containerapp create \
  -n $APP -g $RG --environment $APP_ENV \
  --image $ACR.azurecr.io/vibeshub:latest \
  --registry-server $ACR.azurecr.io --registry-identity $MI_ID \
  --user-assigned $MI_ID \
  --ingress external --target-port 8000 \
  --min-replicas 1 --max-replicas 3 \
  --env-vars \
    VIBESHUB_DATABASE_URL="$DATABASE_URL" \
    VIBESHUB_AZURE_BLOB_CONTAINER="$CONTAINER" \
    VIBESHUB_AZURE_STORAGE_ACCOUNT_URL="$ACCOUNT_URL" \
    AZURE_CLIENT_ID="$MI_CLIENT_ID" \
    VIBESHUB_PUBLIC_BASE_URL="$PUBLIC_URL" \
    VIBESHUB_GITHUB_OAUTH_CLIENT_ID="$OAUTH_CLIENT_ID" \
    VIBESHUB_GITHUB_OAUTH_CLIENT_SECRET="$OAUTH_CLIENT_SECRET" \
    VIBESHUB_GITHUB_FALLBACK_TOKEN="$GITHUB_FALLBACK_TOKEN" \
    VIBESHUB_SESSION_SECRET="$SESSION_SECRET" \
    VIBESHUB_TOKEN_ENCRYPTION_KEY="$TOKEN_ENCRYPTION_KEY"
```

Notes:
- `AZURE_CLIENT_ID` tells `DefaultAzureCredential` which user-assigned identity to use when multiple are bound.
- The container's `CMD` runs `alembic upgrade head` before starting uvicorn, so the schema is created on first boot.
- The image must be built with the `[azure]` extra — [./Dockerfile](./Dockerfile) does `pip install -e ".[azure]"` which pulls in `azure-storage-blob`, `azure-identity`, and `aiohttp` (used by azure-identity's async transport).
- Register the GitHub OAuth app at <https://github.com/settings/developers> with the authorization callback URL set to `$PUBLIC_URL/api/auth/github/callback`. Update both the GitHub app and `VIBESHUB_PUBLIC_BASE_URL` after the first deploy returns a real FQDN.

After the first deploy, grab the actual FQDN and update `VIBESHUB_PUBLIC_BASE_URL` so the trace URLs returned by `/api/ingest` are correct:

```bash
FQDN=$(az containerapp show -n $APP -g $RG --query properties.configuration.ingress.fqdn -o tsv)
az containerapp update -n $APP -g $RG \
  --set-env-vars VIBESHUB_PUBLIC_BASE_URL="https://$FQDN"
```

## 3b. (Optional) Automate deploys from GitHub Actions

[`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml) runs `deploy/azure/deploy.sh` on every push to `main`. To use it in a fork, set four repository secrets:

- `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` — an app registration with a GitHub OIDC federated credential and Contributor on the resource group. **This client ID is the CI service principal, not the container's managed-identity `AZURE_CLIENT_ID` env var above.**
- `DEPLOY_ENV_FILE` — the full contents of your `deploy/azure/.env`.

## 4. Configure the plugin to point at your deployment

On each developer's machine:

```bash
export VIBESHUB_SERVER_URL="https://$FQDN"
```

Then install the vibeshub plugin (Claude Code, Codex, or Cursor) per [../../plugins/cli/README.md](../../plugins/cli/README.md).

## Environment variables (reference)

| Var | Purpose |
|---|---|
| `VIBESHUB_DATABASE_URL` | `postgresql+psycopg://…` Postgres DSN; must include `sslmode=require` on Azure |
| `VIBESHUB_AZURE_BLOB_CONTAINER` | Blob container name — presence switches storage from local disk to Azure |
| `VIBESHUB_AZURE_STORAGE_ACCOUNT_URL` | `https://<account>.blob.core.windows.net`; auths via managed identity |
| `VIBESHUB_AZURE_STORAGE_CONNECTION_STRING` | Alternative auth (account key or Azurite); ignored if the account URL is set |
| `AZURE_CLIENT_ID` | Client ID of the user-assigned MI when more than one is bound |
| `VIBESHUB_PUBLIC_BASE_URL` | Origin used to build the `trace_url` returned by the API **and** the GitHub OAuth `redirect_uri`; must exactly match the callback URL registered on the OAuth app |
| `VIBESHUB_GITHUB_OAUTH_CLIENT_ID` / `_SECRET` | GitHub OAuth app credentials, required for the "Sign in with GitHub" flow; if the client ID (or `VIBESHUB_SESSION_SECRET`) is empty, `/api/auth/github/login` and `/api/auth/github/callback` return `503 oauth_not_configured` (`/api/auth/me` and `/logout` still work) |
| `VIBESHUB_GITHUB_FALLBACK_TOKEN` | Server-side PAT used to read public GitHub data for anonymous viewers; no scopes needed |
| `VIBESHUB_SESSION_SECRET` | Signs the short-lived OAuth state cookie (`python -c 'import secrets; print(secrets.token_urlsafe(48))'`) |
| `VIBESHUB_TOKEN_ENCRYPTION_KEY` | Fernet key used to encrypt stored OAuth access tokens (`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`); comma-separate `new,old` to rotate |
| `VIBESHUB_COOKIE_SECURE` | Defaults to `true`; leave on for Azure |

See [../../webapp/backend/README.md](../../webapp/backend/README.md) for the full list (core, OAuth, size limits, digest OpenAI vars). Digest agent details: [../../webapp/backend/app/agents/digest/README.md](../../webapp/backend/app/agents/digest/README.md).

## Troubleshooting

The container runs a startup credential smoke-check before serving traffic: it verifies the DB is reachable (`SELECT 1`) and the Azure Blob container is reachable (`get_container_properties`). If a deploy is marked unhealthy immediately and rolls back, check the container log stream. Passing checks log `smoke-check: db OK` / `smoke-check: blob OK`; a failure instead surfaces as an uncaught `SmokeCheckError` traceback whose message begins with `db:` or `blob:` followed by the underlying error (passwords in the DSN are redacted). A `smoke-check: db OK` line with no `smoke-check: blob OK` after it means blob was the failure.

Operators no longer need to run `check_db.py` for routine "is DB reachable" verification — the startup logs cover it. `check_db.py` is still useful for deeper one-off diagnosis.
