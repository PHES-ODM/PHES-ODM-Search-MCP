# Running PHES-ODM Search MCP in Docker

This guide covers building and running the server as a Docker container, and deploying it publicly on an AWS EC2 instance running Debian Linux.

---

## Contents

- [Running PHES-ODM Search MCP in Docker](#running-phes-odm-search-mcp-in-docker)
  - [Contents](#contents)
  - [Quick start (local)](#quick-start-local)
  - [Image overview](#image-overview)
  - [Deploying on AWS EC2 (Debian Linux)](#deploying-on-aws-ec2-debian-linux)
    - [1. Launch an EC2 instance](#1-launch-an-ec2-instance)
    - [2. Connect and prepare the instance](#2-connect-and-prepare-the-instance)
    - [3. Install Docker](#3-install-docker)
    - [4. Copy the project to the server](#4-copy-the-project-to-the-server)
    - [5. Build the Docker image](#5-build-the-docker-image)
    - [6. Configure nginx](#6-configure-nginx)
    - [7. Start the services](#7-start-the-services)
    - [8. Open the firewall (AWS Security Group)](#8-open-the-firewall-aws-security-group)
    - [9. Verify the server is reachable](#9-verify-the-server-is-reachable)
    - [10. Connect an MCP client](#10-connect-an-mcp-client)
    - [11. Optional — TLS with Let's Encrypt](#11-optional--tls-with-lets-encrypt)
  - [Environment variables](#environment-variables)
  - [Maintenance](#maintenance)
    - [View logs](#view-logs)
    - [Restart the server](#restart-the-server)
    - [Rebuild the embeddings index](#rebuild-the-embeddings-index)
    - [Update to a new version](#update-to-a-new-version)

---

## Quick start (local)

Build and run everything locally using Docker Compose:

```bash
docker compose up --build
```

The HTTP endpoint will be available at `http://localhost/mcp`.

To stop:

```bash
docker compose down
```

---

## Image overview

The `Dockerfile` uses a two-stage build:

| Stage | Purpose |
|-------|---------|
| **builder** | Installs Python dependencies, pre-downloads the `all-MiniLM-L6-v2` sentence-transformers model (~90 MB), and generates the embeddings index from the schema — all baked into the image so the container starts immediately without a network fetch or index build at runtime. |
| **runtime** | Slim final image containing only what is needed to run the server. |

The embeddings index (`embeddings/`) is generated during the image build (~4 MB) and does **not** need to exist in the source tree beforehand. If you update `odm_v3.yaml`, rebuild the image to re-index.

`docker-compose.yml` defines three services:

| Service | Description |
|---------|-------------|
| `mcp` | The FastMCP HTTP server, listening on port 8000 inside the Docker network. |
| `nginx` | Reverse proxy that exposes port 80 (and optionally 443) to the outside world. |
| `certbot` | One-shot container used to obtain a Let's Encrypt TLS certificate (disabled by default via Docker Compose profiles). |

---

## Deploying on AWS EC2 (Debian Linux)

### 1. Launch an EC2 instance

1. Open the **EC2 console** and choose **Launch instance**.
2. Select **Debian 12 (Bookworm)** from the AWS Marketplace or Community AMIs.
3. Choose an instance type. The server requires at least **1 GB RAM** — `t3.micro` (1 GB) is sufficient for light usage; `t3.small` (2 GB) is more comfortable.
4. Under **Storage**, allocate at least **12 GB** (OS + Docker images + PyTorch stack).
5. Under **Network settings**, create or select a Security Group. You will open ports in [step 8](#8-open-the-firewall-aws-security-group).
6. Generate or select an SSH key pair and launch the instance.
7. Note the instance's **Public IPv4 address** (or associate an Elastic IP for a stable address).

---

### 2. Connect and prepare the instance

SSH into the instance (replace `<PUBLIC-IP>` with your instance's address):

```bash
ssh -i ~/.ssh/your-key.pem admin@<PUBLIC-IP>
```

> **Note:** The default user on AWS Debian AMIs is `admin`, not `ec2-user` or `ubuntu`.

Update the system:

```bash
sudo apt update && sudo apt upgrade -y
```

---

### 3. Install Docker

Install Docker Engine and Docker Compose on Debian 12:

```bash
# Add Docker's official GPG key and repository
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

Add your user to the `docker` group so you can run Docker commands without `sudo`:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

Verify:

```bash
docker --version
docker compose version
```

---

### 4. Copy the project to the server

**Option A — git clone (if the repo is hosted)**

```bash
git clone <repo-url> ~/PHES-ODM-Search-MCP
cd ~/PHES-ODM-Search-MCP
```

**Option B — rsync from your local machine**

Run this on your **local** machine:

```bash
rsync -av --exclude '__pycache__' --exclude '.git' \
    /path/to/PHES-ODM-Search-MCP/ \
    admin@<PUBLIC-IP>:~/PHES-ODM-Search-MCP/
```

Then on the server:

```bash
cd ~/PHES-ODM-Search-MCP
```

---

### 5. Build the Docker image

The first build downloads the sentence-transformers model and compiles PyTorch dependencies. This can take **5–10 minutes** depending on network speed.

```bash
docker compose build
```

---

### 6. Configure nginx

Open `nginx.conf` and replace `YOUR_DOMAIN` with your server's public IP address or DNS name in the `server_name` directive of the HTTP block:

```bash
nano nginx.conf
```

For example, if your Elastic IP is `52.10.20.30`:

```nginx
server {
    listen 80;
    server_name 52.10.20.30;
    ...
}
```

If you have a domain name, use that instead (required for TLS — see [step 11](#11-optional--tls-with-lets-encrypt)).

> **Note:** The default `server_name _;` (wildcard) works too if you only have one site on the server and do not plan to use TLS.

---

### 7. Start the services

```bash
docker compose up -d
```

Check that both containers started cleanly:

```bash
docker compose ps
docker compose logs mcp
```

You should see a line similar to:

```
INFO:__main__:Server ready — 2322 parts indexed, model=all-MiniLM-L6-v2
```

---

### 8. Open the firewall (AWS Security Group)

In the **EC2 console**, navigate to **Security Groups** → select the group attached to your instance → **Edit inbound rules** → add:

| Type | Protocol | Port range | Source |
|------|----------|------------|--------|
| HTTP | TCP | 80 | 0.0.0.0/0 (or restrict to specific IPs) |
| HTTPS | TCP | 443 | 0.0.0.0/0 (if using TLS) |

Save the rules.

---

### 9. Verify the server is reachable

From your local machine:

```bash
curl http://<PUBLIC-IP>/mcp
```

You should see the server respond to the HTTP request.

---

### 10. Connect an MCP client

#### Claude Desktop

Edit `claude_desktop_config.json` on your local machine:

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Add (or merge):

```json
{
  "mcpServers": {
    "phes-odm-search": {
      "url": "http://<PUBLIC-IP>/mcp"
    }
  }
}
```

Restart Claude Desktop.

#### Claude Code CLI

```bash
claude mcp add phes-odm-search --transport http http://<PUBLIC-IP>/mcp
```

---

### 11. Optional — TLS with Let's Encrypt

HTTPS is strongly recommended for a public server. You need a **domain name** with an A record pointing to the instance's public IP before proceeding.

**Step 1 — Run Certbot to obtain a certificate**

```bash
docker compose run --rm certbot certonly \
    --webroot \
    --webroot-path /var/www/certbot \
    -d YOUR_DOMAIN \
    --email your@email.com \
    --agree-tos \
    --no-eff-email
```

Certbot stores the certificate files in the `certbot-conf` Docker volume, which is also mounted in the nginx container.

**Step 2 — Enable the HTTPS server block in nginx.conf**

Edit `nginx.conf`:

```bash
nano nginx.conf
```

1. In the HTTP `server` block, replace the `proxy_pass` location block with a redirect:
   ```nginx
   location / {
       return 301 https://$host$request_uri;
   }
   ```

2. Uncomment the entire `# HTTPS` server block at the bottom of the file, replacing `YOUR_DOMAIN` with your actual domain name.

**Step 3 — Reload nginx**

```bash
docker compose exec nginx nginx -s reload
```

Verify with:

```bash
curl https://YOUR_DOMAIN/mcp
```

**Step 4 — Update client URLs**

Update your MCP client configuration to use `https://YOUR_DOMAIN/mcp`.

**Automatic renewal**

Certbot certificates expire after 90 days. Add a cron job on the EC2 instance to renew automatically:

```bash
crontab -e
```

Add:

```cron
0 3 * * * cd ~/PHES-ODM-Search-MCP && \
    docker compose run --rm --profile certbot certbot renew --quiet && \
    docker compose exec nginx nginx -s reload
```

---

## Environment variables

The MCP server reads configuration from these environment variables (all have sensible defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `ODM_SCHEMA` | `odm_search_mcp/data/schemas/odm_v3.yaml` | Path to the LinkML schema file |
| `ODM_STORE` | `embeddings` | Directory for the cached embeddings index |
| `ODM_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model name |
| `ODM_HOST` | `0.0.0.0` | Host the HTTP server binds to (set automatically by Docker) |
| `ODM_PORT` | `8000` | Port the HTTP server listens on |

Override any variable in `docker-compose.yml` under the `mcp.environment` key.

---

## Maintenance

### View logs

```bash
# Live log stream
docker compose logs -f mcp

# Last 100 lines
docker compose logs --tail=100 mcp
```

### Restart the server

```bash
docker compose restart mcp
```

### Rebuild the embeddings index

The embeddings are baked into the image. To rebuild after updating the schema:

```bash
# Rebuild the image (re-indexes during build)
docker compose build --no-cache mcp
docker compose up -d
```

Alternatively, to rebuild the index in the running container without a full
image rebuild (updates are lost when the container restarts unless you mount
a volume):

```bash
docker compose exec mcp \
    python -m odm_search_mcp.server --rebuild
```

The process exits automatically once the index is written to disk.

To persist a rebuilt index across container restarts, uncomment the `embeddings` volume lines in `docker-compose.yml`.

### Update to a new version

```bash
git pull                        # or rsync new files
docker compose build mcp
docker compose up -d
```
