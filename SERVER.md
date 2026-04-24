# Deploying the PHES-ODM Search MCP Server on Debian Linux

These instructions set up the server on a Debian-based host (e.g. an AWS EC2
instance running Debian 12 "Bookworm" or Ubuntu 22.04 LTS).
The server supports three transports:

| Transport       | Flag               | Endpoint | Notes                              |
| --------------- | ------------------ | -------- | ---------------------------------- |
| Streamable HTTP | `--transport http` | `/mcp`   | Recommended; modern MCP transport  |
| SSE + HTTP      | `--transport sse`  | `/sse`   | Legacy SSE; needs buffering off    |
| stdio           | `--transport stdio`| —        | Local use only; no network needed  |

The steps below use nginx as a reverse proxy for the network transports.
Differences between the two HTTP-based transports are called out inline.

---

## Contents

- [Deploying the PHES-ODM Search MCP Server on Debian Linux](#deploying-the-phes-odm-search-mcp-server-on-debian-linux)
  - [Contents](#contents)
  - [Prerequisites](#prerequisites)
  - [1. Create a dedicated user](#1-create-a-dedicated-user)
  - [2. Install Python](#2-install-python)
  - [3. Deploy the application](#3-deploy-the-application)
  - [4. Install Python dependencies](#4-install-python-dependencies)
  - [5. Pre-build the embeddings index](#5-pre-build-the-embeddings-index)
  - [6. Create a systemd service](#6-create-a-systemd-service)
  - [7. Install and configure nginx](#7-install-and-configure-nginx)
  - [8. Open the firewall](#8-open-the-firewall)
  - [9. TLS with Let's Encrypt](#9-tls-with-lets-encrypt)
  - [10. Connect an MCP client](#10-connect-an-mcp-client)
    - [Claude Desktop](#claude-desktop)
    - [Claude Code CLI](#claude-code-cli)
  - [Maintenance](#maintenance)
    - [Rebuilding the embeddings index](#rebuilding-the-embeddings-index)

---

## Prerequisites

- A Debian 12 or Ubuntu 22.04 host with at least **1 GB RAM** and **2 GB free disk space** (the embedding model is ~90 MB; the full sentence-transformers stack with PyTorch is larger).
- If using AWS EC2, a Debian Linux instance should have at least 12 GB free disk space.
- A non-root user with `sudo` access.
- The host's **public IP address** or a DNS name pointing to it (needed for nginx and the client configuration).
- Ports **80** (and **443** if using TLS) reachable from clients — open these in your AWS Security Group or equivalent firewall.

---

## 1. Create a dedicated user

Run the following as `root` or with `sudo`:

```bash
sudo useradd --system --create-home --shell /bin/bash odm
```

All application files will live under `/home/odm/`.

---

## 2. Install Python

The server requires Python 3.10 or later.

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git
```

Verify:

```bash
python3 --version   # should print 3.10+
```

---

## 3. Deploy the application

Copy the project directory to the server and place it under the service account's home directory.

**Option A — git clone (if the repo is hosted)**

With HTTPS:

```bash
sudo -u odm git clone https://github.com/PHES-ODM/PHES-ODM-Search-MCP.git /home/odm/PHES-ODM-Search-MCP
```

Or with SSH:

```bash
sudo -u odm git clone git@github.com:PHES-ODM/PHES-ODM-Search-MCP.git /home/odm/PHES-ODM-Search-MCP
```

**Option B — copy from a local machine**

```bash
# Run on your local machine
rsync -av --exclude '__pycache__' --exclude 'embeddings/' \
    /path/to/PHES-ODM-Search-MCP/ user@<server-ip>:/tmp/PHES-ODM-Search-MCP/

# Then on the server
sudo mv /tmp/PHES-ODM-Search-MCP /home/odm/PHES-ODM-Search-MCP
sudo chown -R odm:odm /home/odm/PHES-ODM-Search-MCP
```

---

## 4. Install Python dependencies

Create an isolated virtual environment and install the requirements:

```bash
sudo -u odm bash -c "
    python3 -m venv /home/odm/venv
    mkdir -p /home/odm/tmp
    TMPDIR=/home/odm/tmp /home/odm/venv/bin/pip install \
        -r /home/odm/PHES-ODM-Search-MCP/requirements.txt
"
```

The `sentence-transformers` package pulls in PyTorch (CPU build) and several other libraries; expect the download to take a few minutes.

---

## 5. Pre-build the embeddings index

The first time the server starts it downloads the embedding model (~90 MB)
and encodes ~2 300 parts.  Doing this once now avoids a slow first startup
under systemd:

```bash
sudo -u odm bash -c "cd /home/odm/PHES-ODM-Search-MCP && \
    /home/odm/venv/bin/python -m odm_search_mcp.server --rebuild"
```

The process exits automatically once you see:

```
INFO:odm_search_mcp.server:Rebuild complete — 2169 parts indexed, model=all-MiniLM-L6-v2
```

The encoded vectors are saved to `/home/odm/PHES-ODM-Search-MCP/embeddings/`
and will be reused on every subsequent start.

---

## 6. Create a systemd service

Create the unit file:

```bash
sudo tee /etc/systemd/system/PHES-ODM-Search-MCP.service > /dev/null <<'EOF'
[Unit]
Description=PHES-ODM Embedding Search MCP Server
After=network.target

[Service]
Type=simple
User=odm
WorkingDirectory=/home/odm/PHES-ODM-Search-MCP
ExecStart=/home/odm/venv/bin/python -m odm_search_mcp.server --transport http
# To use the SSE transport instead, replace the line above with:
# ExecStart=/home/odm/venv/bin/python -m odm_search_mcp.server --transport sse
Restart=on-failure
RestartSec=5

# Tighten the process sandbox
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now PHES-ODM-Search-MCP
sudo systemctl status PHES-ODM-Search-MCP
```

The server listens on **127.0.0.1:8000** by default.  It is not exposed directly to the network; nginx handles all external traffic.

---

## 7. Install and configure nginx

Install nginx:

```bash
sudo apt install -y nginx
```

Create a virtual-host configuration.  Replace `your.domain.example` with your server's public IP address or DNS name:

```bash
sudo tee /etc/nginx/sites-available/PHES-ODM-Search-MCP > /dev/null <<'EOF'
server {
    listen 80;
    server_name your.domain.example;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;

        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
EOF
```

Enable the site, disable the default site that ships with nginx, and reload:

```bash
sudo ln -s /etc/nginx/sites-available/PHES-ODM-Search-MCP \
           /etc/nginx/sites-enabled/PHES-ODM-Search-MCP
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

---

## 8. Open the firewall

If the host uses `ufw`:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp   # if you plan to add TLS later
sudo ufw reload
```

If the host is an **AWS EC2** instance, update the instance's Security Group to allow inbound traffic on port 80 (and 443) from the relevant source IP ranges.

---

## 9. TLS with Let's Encrypt

Serving over HTTPS is strongly recommended when the server is accessible from
the public internet.

Install Certbot:

```bash
sudo apt install -y certbot python3-certbot-nginx
```

Obtain and install a certificate (requires a valid DNS name pointing to the server):

```bash
sudo certbot --nginx -d your.domain.example
```

Certbot automatically modifies the nginx configuration and sets up automatic
renewal.  Reload nginx after the certificate is issued:

```bash
sudo systemctl reload nginx
```

---

## 10. Connect an MCP client

With the server running and nginx in place, MCP clients connect over HTTP
using the URL:

```text
https://your.domain.example/mcp
```

### Claude Desktop

Edit `claude_desktop_config.json` on the client machine.  The file location
depends on the operating system:

| OS | Path |
| -- | ---- |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Add (or merge) the following:

```json
{
  "mcpServers": {
    "phes-odm-search": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your.domain.example/mcp"
      ]
    }
  }
}
```

### Claude Code CLI

```bash
claude mcp add phes-odm-search --transport http http://your.domain.example/mcp
```

---

## Maintenance

| Task | Command |
|------|---------|
| View live logs | `sudo journalctl -u PHES-ODM-Search-MCP -f` |
| Restart the server | `sudo systemctl restart PHES-ODM-Search-MCP` |
| Rebuild the embeddings index | See below |
| Update the application | Pull / copy new files, then `sudo systemctl restart PHES-ODM-Search-MCP` |
| Change the embedding model | Update `ODM_MODEL` in the unit file and rebuild |

### Rebuilding the embeddings index

Always use the virtual-environment Python (`/home/odm/venv/bin/python`), not the
system `python3`.  Using the wrong interpreter will produce
`ModuleNotFoundError: No module named 'mcp'`.

```bash
sudo systemctl stop PHES-ODM-Search-MCP
sudo -u odm bash -c "cd /home/odm/PHES-ODM-Search-MCP && \
    /home/odm/venv/bin/python -m odm_search_mcp.server --rebuild"
sudo systemctl start PHES-ODM-Search-MCP
```

The rebuild command exits automatically once the index is written to disk.
