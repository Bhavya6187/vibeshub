# Deploying vibeshub to Azure (Portal, no CLI)

End-to-end walkthrough using only the Azure Portal at [https://portal.azure.com](https://portal.azure.com). Companion to [README.md](README.md) (CLI version). Env var reference: [`.env.example`](./.env.example).

You will create six resources, all in the **same region** (e.g. East US) and **same resource group**:

1. Resource group
2. Container Registry (ACR)
3. PostgreSQL Flexible Server
4. Storage Account + Blob container
5. User-assigned Managed Identity
6. Container Apps Environment + Container App

Keep a scratch pad open — you'll copy ~6 values between steps.

---

## 1. Resource group

1. Top search bar → **Resource groups** → **+ Create**.
2. Name: `vibeshub-rg`. Region: pick one (e.g. **East US**). **Review + create** → **Create**.

> Use this region for every resource below.

## 2. Container Registry

1. Search **Container registries** → **+ Create**.
2. Resource group: `vibeshub-rg`. Registry name: globally unique, lowercase, e.g. `vibeshubacr<yourname>`. SKU: **Basic**.
3. **Review + create** → **Create**.
4. After deploy, open the registry → **Settings → Access keys** → leave **Admin user** **disabled**. (We'll auth with managed identity.)
5. **Scratch pad:** copy the **Login server** (`vibeshubacr<…>.azurecr.io`).

## 3. PostgreSQL Flexible Server

1. Search **Azure Database for PostgreSQL flexible servers** → **+ Create** → **Flexible server**.
2. Basics:
  - Resource group: `vibeshub-rg`
  - Server name: globally unique, e.g. `vibeshub-pg-<yourname>`
  - PostgreSQL version: **16**
  - Workload: **Development** (Burstable, **Standard_B1ms**, 32 GB)
  - Admin username: `vibeshub`
  - Password: strong password — **save it on the scratch pad**.
3. **Networking** tab:
  - Connectivity method: **Public access (allowed IP addresses)**
  - Tick **Allow public access from any Azure service within Azure to this server**.
  - (We're skipping VNet integration for simplicity. For production-only access, add a Private Endpoint instead.)
4. **Review + create** → **Create**. Wait ~5 min.
5. When deployed: open the server → **Databases** → **+ Add** → name it `vibeshub` → **Save**.
6. **Scratch pad:** copy the **Server name** (the full FQDN `vibeshub-pg-<…>.postgres.database.azure.com`) from the overview page.

## 4. Storage account + blob container

1. Search **Storage accounts** → **+ Create**.
2. Resource group: `vibeshub-rg`. Storage account name: globally unique, lowercase, no dashes, e.g. `vibeshubstor<yourname>`. Performance: **Standard**. Redundancy: **LRS**.
3. **Review + create** → **Create**. Wait ~1 min.
4. Open the account → **Data storage → Containers** → **+ Container**. Name: `traces`. Anonymous access: **Private (no anonymous access)**. **Create**.
5. **Scratch pad:** copy the **Blob service endpoint** (`https://vibeshubstor<…>.blob.core.windows.net`) — find it under **Settings → Endpoints**.

## 5. User-assigned managed identity

1. Search **Managed Identities** → **+ Create**.
2. Resource group: `vibeshub-rg`. Name: `vibeshub-mi`. **Review + create** → **Create**.
3. Open the identity. **Scratch pad:** copy the **Client ID** (Overview page) — this is what goes into `AZURE_CLIENT_ID`.

### Grant the identity access to the storage account

1. Go back to the storage account from step 4.
2. Left nav → **Access control (IAM)** → **+ Add** → **Add role assignment**.
3. Role tab: search **Storage Blob Data Contributor** → **Next**.
4. Members tab: Assign access to **Managed identity** → **+ Select members** → Managed identity: **User-assigned managed identity** → pick `vibeshub-mi` → **Select** → **Next**.
5. **Review + assign**.

### Grant the identity pull access to ACR

1. Go to the Container Registry from step 2.
2. **Access control (IAM)** → **+ Add** → **Add role assignment**.
3. Role: **AcrPull** → assign to `vibeshub-mi` (same flow as above).

## 6. Build the image (no Docker on your laptop)

Use ACR Tasks to build directly from your repo — no Docker required.

1. Push the repo to GitHub if you haven't already, and make sure `webapp/frontend` has been built once. Easiest approach: connect ACR Tasks to a GitHub Actions workflow that runs `npm run build:deploy` in `webapp/frontend` first, then triggers the ACR build. If you're starting from a clean repo, the quickest UI-only path is:
  - Open the registry → **Services → Tasks** → **+ Add**.
  - Task name: `build-vibeshub`. Source: **GitHub** (authorize once). Repo: your fork. Branch: `main`. **Source context path: `webapp/backend`** (the Dockerfile `COPY`s `pyproject.toml`, `app/`, `alembic/`, and `frontend_dist/`, all of which live there; a repo-root context fails on the first `COPY`). Dockerfile path: `deploy/azure/Dockerfile`. Image: `vibeshub:{{.Run.ID}}` and also tag `vibeshub:latest`.
  - Under **Source triggers** enable **Commit** so a push rebuilds.
  - Save, then **Run task** once manually.
2. **Important:** the Dockerfile expects pre-built frontend assets at `webapp/backend/frontend_dist/`, and that directory is **gitignored** (see `.gitignore`), so it is never present in a GitHub-sourced ACR Task build. Either (a) add a GitHub Actions workflow that runs `npm ci && npm run build:deploy` in `webapp/frontend` and then invokes `az acr build` with `webapp/backend` as the context — this is what [`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml) + [`deploy.sh`](./deploy.sh) already do — or (b) skip ACR Tasks and use the Cloud Shell one-liner below, which builds from a working tree where you have run the frontend build, rather than from git.
3. After the task succeeds, open **Services → Repositories → vibeshub** and confirm the `latest` tag exists.

> If you'd rather not wire up ACR Tasks, the single command `az acr build -r <acr-name> -t vibeshub:latest --file deploy/azure/Dockerfile webapp/backend` (run in Azure Cloud Shell from the portal — top-right `>`_ icon, with the repo cloned in Cloud Shell first) builds and pushes from the browser without installing anything locally. Cloud Shell still counts as "all in the browser."

> **Building locally with Docker?** Container Apps requires `linux/amd64`, so on Apple Silicon (or any arm64 host) you must cross-build — a plain `docker build` produces an arm64 image that the portal will reject with *"Selected tag uses an invalid architecture 'arm64'."* Use buildx to build and push in one step:
>
> ```bash
> az acr login -n <acr-name>
> docker buildx build --platform linux/amd64 \
>   -t <acr-name>.azurecr.io/vibeshub:latest \
>   --file deploy/azure/Dockerfile \
>   --push webapp/backend
> ```

## 7. Container App (and its environment, inline)

The Container App wizard can create the environment inline, which is the simplest path. (You can also create a **Container Apps Environment** standalone first and select it here.)

1. Search **Container Apps** → **+ Create** → **Container App**.
2. **Basics**:
  - Resource group: `vibeshub-rg`
  - Container app name: `vibeshub`
  - Region: same
  - **Container Apps Environment** → click **Create new** → name `vibeshub-env`, Zone redundancy **Disabled** → **Create**. The new environment is then auto-selected on the Basics tab.
3. **Container** tab:
  - Untick **Use quickstart image**.
  - Image source: **Azure Container Registry**
  - Registry: pick your ACR. Image: `vibeshub`. Tag: `latest`.
  - **Authentication: Managed identity** → choose `vibeshub-mi`.
  - CPU / memory: 0.5 vCPU / 1 Gi (fine for low traffic).
  - **Environment variables** — add each row using the values from [`.env.example`](./.env.example):

    | Name                                   | Value                                                                              |
    | -------------------------------------- | ---------------------------------------------------------------------------------- |
    | `VIBESHUB_DATABASE_URL`                | `postgresql+psycopg://vibeshub:<password>@<pg-fqdn>:5432/vibeshub?sslmode=require` |
    | `VIBESHUB_AZURE_BLOB_CONTAINER`        | `traces`                                                                           |
    | `VIBESHUB_AZURE_STORAGE_ACCOUNT_URL`   | the blob endpoint from step 4                                                      |
    | `AZURE_CLIENT_ID`                      | the MI client ID from step 5                                                       |
    | `VIBESHUB_PUBLIC_BASE_URL`             | leave as `https://placeholder` for now — we'll fix it in step 8                    |
    | `VIBESHUB_GITHUB_OAUTH_CLIENT_ID`      | from your GitHub OAuth app (see below)                                             |
    | `VIBESHUB_GITHUB_OAUTH_CLIENT_SECRET` | from your GitHub OAuth app                                                         |
    | `VIBESHUB_GITHUB_FALLBACK_TOKEN`       | any GitHub PAT (no scopes needed) — used to read public data for anonymous viewers |
    | `VIBESHUB_SESSION_SECRET`              | run `python -c 'import secrets; print(secrets.token_urlsafe(48))'`                 |
    | `VIBESHUB_TOKEN_ENCRYPTION_KEY`        | run `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |

    To enable the optional AI trace digest, also add `VIBESHUB_OPENAI_API_KEY`, `VIBESHUB_OPENAI_ENDPOINT`, and `VIBESHUB_OPENAI_MODEL`. They must be set together: with any one missing, uploads still succeed and the viewer just hides the digest panel. Full reference: [`webapp/backend/README.md`](../../webapp/backend/README.md).

    Both secret-generation commands above need Python. The `secrets` one is stdlib; the Fernet one needs `pip install cryptography` first. If you have no local Python, run both in the portal's **Cloud Shell** (top-right `>_` icon), which has Python 3 preinstalled.

    Register the GitHub OAuth app at <https://github.com/settings/developers>. Set the **Authorization callback URL** to `<your-future-app-fqdn>/api/auth/github/callback` — leave a placeholder for now and update both the app and `VIBESHUB_PUBLIC_BASE_URL` in step 8 once you know the real FQDN.

4. **Ingress** tab:
  - Ingress: **Enabled**
  - Traffic: **Accepting traffic from anywhere**
  - Target port: **8000**
5. **Scale** tab:
  - Min replicas: **1**, Max replicas: **3**. (Min 0 lets the app scale to zero, which re-runs `alembic upgrade head` and the startup dependency checks on every cold start.)
6. **Identity** tab:
  - **User assigned** → **+ Add** → select `vibeshub-mi`.
7. **Review + create** → **Create**. Wait ~2 min.

## 8. Fix the public URL

1. Open the Container App → **Overview** → copy the **Application Url** (e.g. `https://vibeshub.kindwave-1234abcd.eastus.azurecontainerapps.io`).
2. Left nav → **Containers** → **Edit and deploy** → click the container row → **Environment variables**.
3. Edit `VIBESHUB_PUBLIC_BASE_URL` to the URL you just copied → **Save** → **Create** (this rolls a new revision).
4. Back in <https://github.com/settings/developers>, edit your OAuth app's **Authorization callback URL** to `<that-app-url>/api/auth/github/callback`. Without this, `Sign in with GitHub` will fail with a redirect-mismatch error.

## 9. Verify

1. From the Container App overview, click the **Application Url** — you should see the vibeshub frontend.
2. **Monitoring → Log stream** should show `alembic upgrade head` completing and uvicorn listening on `0.0.0.0:8000` on the first boot.
3. **Revisions and replicas** → confirm the latest revision is **Healthy**.
4. If the revision goes **Unhealthy** and rolls back, the startup dependency check failed. In **Log stream**, look for `smoke-check: db OK` / `smoke-check: blob OK`. A failure appears as an error whose message starts with `db:` (Postgres unreachable — recheck the DSN and the "allow Azure services" firewall rule from step 3) or `blob:` (storage unreachable — recheck the **Storage Blob Data Contributor** grant from step 5 and that `AZURE_CLIENT_ID` matches `vibeshub-mi`).

## 10. Point a developer at it

On each developer's machine (one-time):

- macOS / Linux shell: `export VIBESHUB_SERVER_URL="https://<your-app-fqdn>"`
- Windows PowerShell: `$env:VIBESHUB_SERVER_URL = "https://<your-app-fqdn>"`

Then install the vibeshub plugin (Claude Code, Codex, or Cursor) per [../../plugins/cli/README.md](../../plugins/cli/README.md).

---

## Updating the app later

To deploy a new image:

1. Trigger the ACR Task again (registry → **Tasks** → **Run**).
2. Container App → **Revision management** → **Create new revision** → keep all settings, just click **Create**. The new revision pulls the latest tag.

To change an env var:

1. Container App → **Containers** → **Edit and deploy** → edit values → **Save** → **Create**.

## Tearing it all down

Delete the `vibeshub-rg` resource group — every resource above lives inside it, so one click removes everything.