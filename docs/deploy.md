# Deploy

The gig map runs on the tspots instance on DigitalOcean, at
`https://gigmap.dev`. This document records the setup, the commands, the
domain and the SSL, the cleanup loop, and the cron alternative.

## The target

| Item          | Value                                         |
| ------------- | --------------------------------------------- |
| provider      | DigitalOcean                                  |
| instance file | `/Users/boscoh/p/nimago/tspots.instance.json` |
| droplet id    | 548814359                                     |
| ip            | 209.38.23.184                                 |
| region        | syd1                                          |
| size          | s-1vcpu-1gb                                   |
| user          | deploy                                        |
| app name      | gigmap                                        |
| app type      | uv                                            |
| internal port | 8000                                          |
| external port | 443                                           |
| domain        | gigmap.dev                                    |
| url           | `https://gigmap.dev`                          |

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

The script `deploy.sh` in the project root runs the command:

```bash
./deploy.sh           # deploy to the port
./deploy.sh update    # send a change
./deploy.sh --ssl     # the domain and SSL, after the A records exist
```

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

| Path                                 | Content                                |
| ------------------------------------ | -------------------------------------- |
| `/home/deploy/gigmap`                | the app and its `.venv`                |
| `/home/deploy/gigmap/data`           | the saved JSON. See `gigmap/stores.py` |
| `/etc/supervisor/conf.d/gigmap.conf` | the service                            |
| `/var/log/gigmap/access.log`         | the app stdout                         |
| `/var/log/gigmap/error.log`          | the app stderr                         |
| `/etc/nginx/sites-available/gigmap`  | the proxy                              |

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

## The domain and SSL

The app answers on `https://gigmap.dev`. Porkbun is the registrar. `rdomain`
manages the name, and `deployvm` manages the DigitalOcean DNS zone.

| Item       | Value                                                    |
| ---------- | -------------------------------------------------------- |
| name       | `gigmap.dev`                                             |
| registrar  | Porkbun. The keys are in `/Users/boscoh/p/deployvm/.env` |
| registered | 2026-10-12                                               |
| price      | $8.75 for one year, auto-renew                           |
| order      | `12082816`                                               |
| balance    | $21.25 after the purchase                                |

`gigmap.com` and `gigmap.io` were taken. `gigmap.dev` was free.

The steps:

1. Register the name. `rdomain register` charges the account, so run
   `rdomain check gigmap.dev` first. Porkbun must verify the account email
   address and phone number, and the account must hold credit.

   ```bash
   rdomain register gigmap.dev
   ```

2. Set the DigitalOcean nameservers at the registrar. Run the command from
   `/Users/boscoh/p/nimago`, so that `--instance` finds the instance file:

   ```bash
   deployvm nameservers gigmap.dev --instance tspots | rdomain nameservers gigmap.dev
   ```

3. Create the DigitalOcean zone with the `A` records for `@` and `www`:

   ```bash
   deployvm dns gigmap.dev --target tspots
   ```

   Create the zone before the nameserver change takes effect. The
   DigitalOcean nameservers answer `SERVFAIL` for a zone that is not there
   yet.

4. Wait for the registrar nameservers, then check:

   ```bash
   dig +short NS gigmap.dev
   dig +short A gigmap.dev
   ```

5. Deploy again with the domain:

   ```bash
   ./deploy.sh --ssl
   ```

   `deployvm` writes `/etc/nginx/sites-available/gigmap.dev` and runs
   certbot for `gigmap.dev` and `www.gigmap.dev`. The certificate holds both
   names. The app then answers on `https://gigmap.dev`.

The deploy removes the old IP-only site `/etc/nginx/sites-available/gigmap`
and closes port 8080 in UFW. The next section records the state after the
switch.

To verify:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://gigmap.dev/
deployvm probe tspots gigmap.dev
deployvm instance verify tspots --domain gigmap.dev
```

### The tool gaps

`rdomain` and `deployvm` miss some steps. This list records each gap and the
fix.

- **Account verification.** Porkbun answers `VERIFICATION_REQUIRED` on
  `domain/create` until the account email address and phone number are
  verified. `rdomain` has no command for this. Use the web page at
  `https://porkbun.com/account`, or the raw `account/verifyEmail` and
  `account/verifyPhone` calls.
- **Prepaid credit.** `rdomain` cannot add credit, and `register` sends no
  `payWith` field. An API purchase uses account credit only. Add credit at
  `https://porkbun.com/account/credit`.
- **No dry run.** `register` charges the account. There is no `--dry-run`
  flag. Run `rdomain check` first.
- **Four commands only.** `rdomain` has `check`, `register`, `list`, and
  `nameservers`. It cannot renew, transfer, or delete a name. It cannot read
  the balance or the current nameservers.
- **The provider (fixed).** `deployvm nameservers` and `deployvm dns` read
  the provider from `--instance` or `--target`. Pass one of them. `.env`
  sets `DEPLOY_VM_PROVIDER=aws`, so a bare `deployvm nameservers` creates a
  Route 53 zone.
- **A nameserver mismatch stops the run (fixed).** `ensure_dns_matches` in
  `deployvm/server.py` stops with the two nameserver sets, unless
  `--skip-dns` is set.
- **The wait (fixed).** `wait_for_dns` waits for the `A` record, and
  `--dns-timeout` sets the budget (default 300 seconds). No command waits
  for the registrar nameservers to change. The change took about 30 seconds.
- **The old nginx site (fixed).** `remove_catch_all_sites_command()` in
  `deployvm/server.py` names each symbolic link for `grep`, so the old
  port-8080 site is removed.
- **The old port (fixed).** A deploy that moves an app from an IP-only port
  to a domain removes the old nginx site and runs `ufw delete allow
<port>/tcp`.
- **`DOMAIN` in `deploy.sh` is manual.** The script does not read the domain
  from the instance file.
- **The second instance file is manual.**
  `/Users/boscoh/p/nimago/tspots.instance.json` holds the live record. The
  copy at `/Users/boscoh/p/deployvm/tspots.instance.json` is stale.

## Cleanup each day

The app runs the cleanup in its FastAPI lifespan: one time at startup, then
each 24 hours. The data lives under `/home/deploy/gigmap/data`. See
`gigmap/stores.py`.

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

- `~/.local/bin/deployvm` and `~/.local/bin/rdomain` are editable installs of
  `/Users/boscoh/p/deployvm`. The code is the source. `--version` shows stale
  metadata.
- `rdomain` reads `/Users/boscoh/p/deployvm/.env` from any directory. You can
  also export `PORKBUN_API_KEY` and `PORKBUN_SECRET_KEY` in the shell.
- A second copy of the instance file sits at
  `/Users/boscoh/p/deployvm/tspots.instance.json`. It lists only `tspots`. Run
  the commands from `nimago`, or copy the new file over it.
- A subdomain of a name that is already on DigitalOcean needs no registrar
  change. Add the `A` records in the existing zone. This is the case for
  `gig.tangiblespots.com`.
- The `rdomain` endpoint faults are fixed in `deployvm/rdomain.py`: the check
  and register endpoints, the nested `response` fields, and the price in
  cents. The `list` command no longer shadows the builtin.

## The state after the first deploy

Date: 2026-10-10, 08:42 local time, 21:42 UTC.

| Check                       | Result                                 |
| --------------------------- | -------------------------------------- |
| `GET /` on port 8080        | 200                                    |
| `GET /api/gigs`             | 200, 1.3 s                             |
| `GET /api/times`            | NDJSON lines arrive                    |
| `supervisorctl status`      | `gigmap RUNNING`                       |
| `/var/log/gigmap/error.log` | the uvicorn startup lines only         |
| `https://tangiblespots.com` | 200. tspots is not affected            |
| pm2                         | `tspots` online                        |
| UFW                         | 8080/tcp allowed. 80 and 443 unchanged |

## The state after the domain switch

Date: 2026-10-12, 17:22 local time, 06:22 UTC.

| Check                                | Result                                                         |
| ------------------------------------ | -------------------------------------------------------------- |
| `https://gigmap.dev/`                | 200                                                            |
| `https://www.gigmap.dev/`            | 200                                                            |
| `http://gigmap.dev/`                 | 301 to `https://gigmap.dev/`                                   |
| `GET /api/gigs` with a date range    | 200, JSON list                                                 |
| certificate                          | `gigmap.dev` and `www.gigmap.dev`, expires 2027-01-10          |
| `dig +short A gigmap.dev` at 8.8.8.8 | `209.38.23.184`                                                |
| `https://tangiblespots.com`          | 200. tspots is not affected                                    |
| `supervisorctl status`               | `gigmap RUNNING`                                               |
| nginx                                | `gigmap.dev`, `ip-access`, and `tangiblespots.com` are enabled |

The port-8080 config `/etc/nginx/sites-available/gigmap` stays enabled. UFW
still allows 8080/tcp.
