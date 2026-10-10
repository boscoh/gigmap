# Deploy

The gig map runs on the tspots instance. This document records the setup, the
commands, the cleanup loop, and the cron alternative.

## The target

| Item | Value |
| --- | --- |
| provider | DigitalOcean |
| instance file | `/Users/boscoh/p/nimago/tspots.instance.json` |
| droplet id | 548814359 |
| ip | 209.38.23.184 |
| region | syd1 |
| size | s-1vcpu-1gb |
| user | deploy |
| app name | gigmap |
| app type | uv |
| internal port | 8000 |
| external port | 8080 |
| url | `http://209.38.23.184:8080` |

The droplet also runs `tspots` (npm, internal port 3000,
`https://tangiblespots.com`).

## The command

Run the command from `/Users/boscoh/p/nimago`. deployvm reads
`{name}.instance.json` from the current directory. A run from another directory
creates a new droplet.

```bash
cd /Users/boscoh/p/nimago
deployvm uv deploy tspots /Users/boscoh/p/gigmap \
  "uv run --no-sync gigmap serve --host 0.0.0.0 --port 8000" \
  --app-name gigmap \
  --port 8000 \
  --outgoing-port 8080
```

The script `deploy.sh` in the project root runs this command.

Do not add `--no-ssl`. That flag belongs to `npm deploy`. For a uv app, omit
`--domain` and the deploy is IP-only.

## What deployvm does

1. Finds `tspots.instance.json` and does not create a droplet.
2. Appends the app to the `apps` array. The file then holds two apps.
3. Uploads the source. It skips `.venv`, `__pycache__`, `.git`, and `*.pyc`.
4. Runs `uv sync --frozen` on the server.
5. Writes `/etc/supervisor/conf.d/gigmap.conf` and starts the app.
6. Opens port 8080 in UFW.
7. Writes `/etc/nginx/sites-available/gigmap` and reloads nginx.
8. Checks `curl http://localhost:8000`.

## The layout on the server

| Path | Content |
| --- | --- |
| `/home/deploy/gigmap` | the app and its `.venv` |
| `/home/deploy/gigmap/data` | the saved JSON. See `docs/cleanup.md` |
| `/etc/supervisor/conf.d/gigmap.conf` | the service |
| `/var/log/gigmap/access.log` | the app stdout |
| `/var/log/gigmap/error.log` | the app stderr |
| `/etc/nginx/sites-available/gigmap` | the proxy |

## Check the app

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://209.38.23.184:8080/
deployvm uv status tspots
deployvm uv logs tspots --app-name gigmap
ssh deploy@209.38.23.184 'sudo supervisorctl status gigmap'
```

## Update the app

```bash
cd /Users/boscoh/p/nimago
deployvm uv sync tspots /Users/boscoh/p/gigmap \
  "uv run --no-sync gigmap serve --host 0.0.0.0 --port 8000" \
  --app-name gigmap
```

`uv sync` compares the source hash. Unchanged source only restarts the app.

## Step A — the domain and SSL

1. Add two A records in the DigitalOcean zone for `tangiblespots.com`:

   | Name | Type | Value |
   | --- | --- | --- |
   | `gig` | A | `209.38.23.184` |
   | `www.gig` | A | `209.38.23.184` |

   Both are necessary. certbot validates `gig.tangiblespots.com` and
   `www.gig.tangiblespots.com`. Add the records before the deploy. Without
   them, deployvm tries to create a new zone for the subdomain.

2. Run the deploy again with the domain:

```bash
cd /Users/boscoh/p/nimago
deployvm uv deploy tspots /Users/boscoh/p/gigmap \
  "uv run --no-sync gigmap serve --host 0.0.0.0 --port 8000" \
  --app-name gigmap \
  --port 8000 \
  --domain gig.tangiblespots.com \
  --email apposite@gmail.com
```

deployvm removes every `sites-enabled` file with `server_name _`, so the port
8080 config from Step B goes away. The app then answers on
`https://gig.tangiblespots.com`.

Close the port later:

```bash
ssh deploy@209.38.23.184 'sudo ufw delete allow 8080/tcp'
```

## Cleanup each day

The app runs the cleanup in its FastAPI lifespan: one time at startup, then
each 24 hours. The data lives under `/home/deploy/gigmap/data`. See
`docs/cleanup.md`.

The server is UTC. The rule uses the local date, so the deploy command pins
the timezone:

```bash
TZ=Australia/Melbourne uv run --no-sync gigmap serve --host 0.0.0.0 --port 8000
```

deployvm parses the leading `TZ=...` pair and writes it into the supervisord
`environment=` line:

```ini
environment=PATH="/home/deploy/.local/bin:...",TZ="Australia/Melbourne"
```

The pair comes from `COMMAND` in `deploy.sh`. It travels with each deploy and
each `update`. No crontab is necessary.

cron remains the alternative for the case where the app is stopped:

```cron
TZ=Australia/Melbourne
0 4 * * * cd /home/deploy/gigmap && /home/deploy/.local/bin/uv run --no-sync gigmap clean --quiet
```

## Test the cleanup on the server

Create a stale file, restart the app, and read the log:

```bash
ssh deploy@209.38.23.184 \
  'f=/home/deploy/gigmap/data/days/gigmap_2020-01-01_2020-01-01.json; printf x > "$f"; touch -t 202001010000 "$f"'
cd /Users/boscoh/p/nimago
deployvm uv restart tspots --app-name gigmap
deployvm uv logs tspots --app-name gigmap --lines 5
```

The log then holds a line like `removed 1 day files and 0 gig files, 1 B`, and
the file is gone. That proves the lifespan in the deployed app.

## Notes

- `~/.local/bin/deployvm` is an editable install of `/Users/boscoh/p/deployvm`.
  The code is the source. `deployvm --version` shows stale metadata.
- A second copy of the instance file sits at
  `/Users/boscoh/p/deployvm/tspots.instance.json`. It lists only `tspots`. Run
  the commands from `nimago`, or copy the new file over it.

## The state after the first deploy

Date: 2026-10-10, 08:42 local time, 21:42 UTC.

| Check | Result |
| --- | --- |
| `GET /` on port 8080 | 200 |
| `GET /api/gigs` | 200, 1.3 s |
| `GET /api/times` | NDJSON lines arrive |
| `supervisorctl status` | `gigmap RUNNING` |
| `/var/log/gigmap/error.log` | the uvicorn startup lines only |
| `https://tangiblespots.com` | 200. tspots is not affected |
| pm2 | `tspots` online |
| UFW | 8080/tcp allowed. 80 and 443 unchanged |
