# The domain

This document records how to get a domain for the gig map, how to check it, and
how to point it at the tspots instance. The instance and the DNS zone live on
DigitalOcean. See `docs/deploy.md` for the instance.

## Candidate names

A check on 2026-10-11 gives these results. The price is US dollars for one
year.

| Name | Free | Price |
| --- | --- | --- |
| `gigmap.com` | no | 11.08 |
| `gigmap.io` | no | 28.12 |
| `gigmap.net` | no | 12.52 |
| `gigmap.org` | no | 7.98 |
| `gigmap.app` | no | 8.75 |
| `gigmap.au` | yes | 8.41 |
| `gigmap.com.au` | yes | 8.41 |
| `gigmap.co` | yes | 15.76 |
| `gigmap.dev` | yes | 8.75 |
| `melbgigmap.com` | yes | 11.08 |
| `melbgigmap.io` | yes | 28.12 |
| `melbgigmap.net` | yes | 12.52 |
| `melbgigmap.org` | yes | 7.98 |
| `melbgigmap.app` | yes | 8.75 |
| `melbgigmap.au` | yes | 8.41 |
| `melbgigmap.com.au` | yes | 8.41 |
| `melbgigmap.xyz` | yes | 2.04 |
| `melbourne-gig-map.com` | yes | 11.08 |
| `melbourne-gig-map.com.au` | yes | 8.41 |
| `melbgigs.com` | yes | 11.08 |
| `melbgigs.au` | yes | 8.41 |

`gigmap.com` and `gigmap.io` are taken. The short name `melbgigmap.com` is
free. Use it, or use `melbgigmap.com.au` for an Australian domain.

## Step 1 — check a name

The registrar is Porkbun. The keys live in
`/Users/boscoh/p/deployvm/.env`.

**Do not use `rdomain check`.** The command calls the endpoint
`domain/check/<name>`. That endpoint does not exist and returns HTTP 404. The
correct endpoint is `domain/checkDomain/<name>`. The command also reads
`avail` and `price` at the top level, but the API nests them under
`response`. Fix `deployvm/rdomain.py` before use. See the Notes.

Check a name with `curl`:

```bash
cd /Users/boscoh/p/deployvm
set -a; . ./.env; set +a
curl -s -X POST \
  "https://api.porkbun.com/api/json/v3/domain/checkDomain/melbgigmap.com" \
  -H 'Content-Type: application/json' \
  -d "{\"apikey\":\"$PORKBUN_API_KEY\",\"secretapikey\":\"$PORKBUN_SECRET_KEY\"}"
```

The answer holds `response.avail` (`yes` or `no`) and `response.price`.
Porkbun limits the check to 10 calls for each 10 seconds. Wait one second
between calls.

To check many names, loop over them:

```bash
cd /Users/boscoh/p/deployvm
set -a; . ./.env; set +a
for d in melbgigmap.com melbgigmap.com.au; do
  printf '%s ' "$d"
  curl -s -X POST \
    "https://api.porkbun.com/api/json/v3/domain/checkDomain/$d" \
    -H 'Content-Type: application/json' \
    -d "{\"apikey\":\"$PORKBUN_API_KEY\",\"secretapikey\":\"$PORKBUN_SECRET_KEY\"}" \
  | python3 -c 'import sys,json; r=json.load(sys.stdin)["response"]; print(r["avail"], r.get("price"))'
  sleep 1
done
```

## Step 2 — register the name

`rdomain register` charges the account. Run it from the deployvm directory,
because `load_dotenv()` finds `.env` relative to the current directory.

```bash
cd /Users/boscoh/p/deployvm
rdomain register melbgigmap.com
```

The command checks the name again, then calls `domain/create`. It prints the
order ID and the account balance. Registration auto-renews.

The `rdomain register` path also uses the broken `domain/check` endpoint, so it
stops with HTTP 404. Fix `deployvm/rdomain.py` first, or register in the
Porkbun web page at `https://porkbun.com`.

List the owned names:

```bash
rdomain list
```

## Step 3 — set the nameservers

The tspots instance uses the DigitalOcean DNS zone. A new name must use the
DigitalOcean nameservers, or `deployvm` cannot manage its records.

Pass `--provider digitalocean` in the first command. Do not use the default.
`.env` sets `DEPLOY_VM_PROVIDER=aws`, so a bare `deployvm nameservers` creates a
Route 53 zone. The DigitalOcean instance cannot use that zone.

```bash
cd /Users/boscoh/p/deployvm
deployvm nameservers melbgigmap.com --provider digitalocean \
  | rdomain nameservers melbgigmap.com
```

`deployvm nameservers` prints a JSON array on stdout. `rdomain nameservers`
reads that array on stdin and calls `domain/updateNs`. The command sets
`ns1.digitalocean.com`, `ns2.digitalocean.com`, and `ns3.digitalocean.com`.

Wait for propagation. Then check:

```bash
dig +short NS melbgigmap.com
dig +short A melbgigmap.com
```

## Step 4 — apply the domain to the app

Two options. For the existing `gigmap` app on `tspots`, add SSL:

```bash
cd /Users/boscoh/p/nimago
deployvm ssl tspots melbgigmap.com apposite@gmail.com --app-name gigmap --port 8000
```

Or deploy again with the domain:

```bash
cd /Users/boscoh/p/nimago
deployvm uv deploy tspots /Users/boscoh/p/gigmap \
  "TZ=Australia/Melbourne uv run --no-sync gigmap serve --host 0.0.0.0 --port 8000" \
  --app-name gigmap \
  --port 8000 \
  --outgoing-port 8080 \
  --domain melbgigmap.com \
  --email apposite@gmail.com
```

The script `deploy.sh` does the same command. Change `DOMAIN` in `deploy.sh`
to the new name, then run `./deploy.sh --ssl`.

`deployvm` creates the DigitalOcean zone, adds the `A` records for `@` and
`www`, writes the nginx config, and runs certbot. Port 8080 then stops.

## Step 5 — verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://melbgigmap.com/
deployvm probe tspots melbgigmap.com
deployvm instance verify tspots --domain melbgigmap.com
```

Close the old port after the switch:

```bash
ssh deploy@209.38.23.184 'sudo ufw delete allow 8080/tcp'
```

## Notes

- The source of `deployvm` is `/Users/boscoh/p/deployvm`. `~/.local/bin/rdomain`
  and `~/.local/bin/deployvm` are editable installs of that source. `rdomain
  --version` shows stale metadata.
- `load_dotenv()` in `rdomain.py` reads `.env` from the current directory and
  its parents. Run `rdomain` from `/Users/boscoh/p/deployvm`, or export
  `PORKBUN_API_KEY` and `PORKBUN_SECRET_KEY` in the shell.
- To fix `rdomain check`, change the endpoint in `_pb_check` and
  `_pb_register` to `domain/checkDomain/<name>`, and read the fields from
  `result["response"]`. Move `import cyclopts` and `from rich import print`
  above `load_dotenv()` to clear the two ruff `E402` errors.
- A subdomain of a name that is already on DigitalOcean needs no registrar
  change. Add the `A` records in the existing zone. This is the case for
  `gig.tangiblespots.com` in `docs/deploy.md`.
