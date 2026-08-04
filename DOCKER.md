# Running PHES-ODM Search MCP in Docker

This guide covers building and running the server as a Docker container, both
locally and on a public Linux server.

---

## Contents

- [Running PHES-ODM Search MCP in Docker](#running-phes-odm-search-mcp-in-docker)
  - [Contents](#contents)
  - [Quick start (local)](#quick-start-local)
  - [What's in the image](#whats-in-the-image)
  - [Deploy on a public server](#deploy-on-a-public-server)
  - [Connect an MCP client](#connect-an-mcp-client)
  - [TLS with Let's Encrypt (optional)](#tls-with-lets-encrypt-optional)
  - [Environment variables](#environment-variables)
  - [Maintenance](#maintenance)
  - [Troubleshooting](#troubleshooting)
    - [Build fails with `exit code: 137` at the `--rebuild` step](#build-fails-with-exit-code-137-at-the---rebuild-step)
    - [Build fails with `no space left on device`](#build-fails-with-no-space-left-on-device)

---

## Quick start (local)

Build and run everything with Docker Compose:

```bash
docker compose up --build      # add -d to run in the background
```

The endpoint is `http://localhost/mcp` (streamable HTTP, the default). To use
the SSE transport instead, set `ODM_TRANSPORT=sse` and the endpoint becomes
`http://localhost/sse`. To stop: `docker compose down`.

---

## What's in the image

The `Dockerfile` is a two-stage build. The **builder** stage installs
dependencies, downloads the `all-MiniLM-L6-v2` model (~90 MB), and generates
the embeddings index — all baked into the image so the container starts
instantly with no runtime download or index build. The **runtime** stage is a
slim final image. If you change `odm_v3.yaml`, rebuild to re-index.

`docker-compose.yml` runs two services: **`mcp`** (the FastMCP server on port
8000, internal to the Docker network) and **`nginx`** (a reverse proxy exposing
ports 80/443). A third, `certbot`, is used only for
[TLS](#tls-with-lets-encrypt-optional) and stays off by default.

---

## Deploy on a public server

**Prerequisites:** any Debian/Ubuntu host (e.g. an AWS EC2 instance) with at
least **2 GB RAM** and a **20–30 GB disk** (a from-scratch build needs transient
space for PyTorch and layer extraction — the default 8 GB volume is too small),
and inbound **ports 80 and 443** open in your firewall / AWS Security Group.

**1. Install Docker** (Docker's official convenience script):

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
```

**2. Get the code:**

```bash
git clone https://github.com/PHES-ODM/PHES-ODM-Search-MCP.git
cd PHES-ODM-Search-MCP
```

**3. Build and start** (the first build downloads PyTorch and the model,
~5–10 min):

```bash
docker compose up --build -d
docker compose logs mcp        # look for "Server ready — 2185 parts indexed"
```

If the build fails with `exit code: 137`, the host ran out of memory during the
index build — see [Troubleshooting](#troubleshooting). If it fails with `no space
left on device`, the host ran out of disk — also see
[Troubleshooting](#troubleshooting).

The server is now reachable at `http://<SERVER-IP>/mcp`. The shipped nginx
config accepts any hostname, so no editing is needed for IP-based HTTP access.

---

## Connect an MCP client

Use `http://<SERVER-IP>/mcp` (or `/sse` if you set `ODM_TRANSPORT=sse`). Once
[TLS](#tls-with-lets-encrypt-optional) is configured, use
`https://<YOUR-DOMAIN>/mcp`.

**Claude Code CLI:**

```bash
claude mcp add phes-odm-search --transport http http://<SERVER-IP>/mcp
```

**Claude Desktop** — edit `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`, Linux:
`~/.config/Claude/`):

```json
{
  "mcpServers": {
    "phes-odm-search": { "url": "http://<SERVER-IP>/mcp" }
  }
}
```

For SSE, use the `/sse` URL and add `"transport": "sse"`. Restart the client
after editing.

---

## TLS with Let's Encrypt (optional)

HTTPS is strongly recommended for a public server. You need a **domain name**
with an A record pointing to the server first.

1. **Obtain a certificate** (replace the domain and email):

   ```bash
   docker compose run --rm certbot certonly --webroot \
       --webroot-path /var/www/certbot \
       -d YOUR_DOMAIN --email your@email.com --agree-tos --no-eff-email
   ```

2. **Enable HTTPS in `nginx.conf`:** uncomment the `# HTTPS` server block at the
   bottom (replacing `YOUR_DOMAIN`), and change the HTTP block's `location /` to
   `return 301 https://$host$request_uri;`.

3. **Reload nginx:** `docker compose exec nginx nginx -s reload`

4. **Auto-renew** — certificates expire after 90 days. Add a cron job
   (`crontab -e`):

   ```cron
   0 3 * * * cd ~/PHES-ODM-Search-MCP && docker compose run --rm certbot renew --quiet && docker compose exec nginx nginx -s reload
   ```

---

## Environment variables

Override any of these in `docker-compose.yml` under `mcp.environment` (all have
sensible defaults):

| Variable | Default | Description |
| --- | --- | --- |
| `ODM_TRANSPORT` | `http` | `http` (streamable HTTP) or `sse` |
| `ODM_SCHEMA` | `odm_search_mcp/data/schemas/odm_v3.yaml` | LinkML schema file |
| `ODM_STORE` | `embeddings` | Cached embeddings index directory |
| `ODM_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model name |
| `ODM_HOST` | `0.0.0.0` | Bind host (set automatically by Docker) |
| `ODM_PORT` | `8000` | Port the server listens on |
| `ODM_BATCH_SIZE` | `64` | Parts encoded per pass when building the index. Lower it (e.g. `8`) to cut peak memory on small hosts; only affects rebuild, not the index itself. |

---

## Maintenance

| Task | Command |
| --- | --- |
| View live logs | `docker compose logs -f mcp` |
| Restart the server | `docker compose restart mcp` |
| Update to a new version | `git pull && docker compose up --build -d` |
| Rebuild the embeddings index | `docker compose build --no-cache mcp && docker compose up -d` |

The embeddings index is baked into the image, so rebuilding the image
re-indexes. To persist a rebuilt index across restarts instead, uncomment the
`embeddings` volume lines in `docker-compose.yml`.

---

## Troubleshooting

### Build fails with `exit code: 137` at the `--rebuild` step

Exit code 137 is a `SIGKILL` — the Linux kernel's out-of-memory (OOM) killer
terminated the process. The `--rebuild` step loads PyTorch and the embedding
model and then encodes every schema part, which briefly needs ~1.5–2 GB of RAM.
Small hosts (e.g. a `t3.micro` with 1 GB and no swap) run out during this spike.

The `Dockerfile` already lowers the batch size and thread count for this step to
keep peak memory down. If the build still OOMs, add swap to the host — the
standard fix for memory-heavy builds on small instances:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab   # persist across reboots
free -h                                                       # verify swap is listed
```

Then rebuild. Alternatives: lower `ODM_BATCH_SIZE` further (e.g. `4`) on the
`--rebuild` line in the `Dockerfile`, use a larger instance for the build, or
build the image on a bigger machine and push it to a registry — the runtime
stage does no rebuild and needs far less memory.

### Build fails with `no space left on device`

The host disk filled up during the build. The error text varies by stage — it
may appear as `[Errno 28] No space left on device` from pip, a `write ...: no
space left on device` from the image export, or a similar message — but they all
mean the same thing. It can surface at **any stage**, not just one — common ones
include:

- **`pip install`** — downloading and unpacking dependencies. The biggest is
  PyTorch: the default `torch` wheel bundles several GB of CUDA/nvidia libraries
  that a CPU-only server never uses. The `Dockerfile` avoids this by installing
  the CPU-only build first (`--index-url https://download.pytorch.org/whl/cpu`).
- **`--rebuild`** — writing the embeddings index and `.pyc` caches.
- **Exporting the image** — Docker/containerd extracting the finished layers
  (e.g. writing under `/var/lib/containerd/.../torch/...`). This needs room for
  the base image, the builder layers, and the extracted runtime image at once.

The cause is the same regardless of stage — a full host disk. Reclaim space and
check free disk on the host:

```bash
df -h /                             # confirm the root volume is near 100%
docker system df                    # how much Docker/containerd is using
docker system prune -af --volumes   # remove unused images, layers, containers
docker buildx prune -af             # BuildKit build cache (often several GB)
```

If the root volume is simply too small, grow it — on AWS, expand the EBS volume
in the console, then resize the partition and filesystem (device names vary;
check `lsblk`):

```bash
sudo growpart /dev/xvda 1
sudo resize2fs /dev/xvda1
df -h /                             # verify the new size
```

A from-scratch build of this image (PyTorch + model + index) needs well more
than the default 8 GB Debian root volume for transient build and extraction
space — budget a **20–30 GB** volume for a reliable clean build.
