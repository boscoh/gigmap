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

`gigmap.com` and `gigmap.io` are taken. We chose `gigmap.dev` at $8.75 for one
year, and registered it on 2026-10-12. The short name `melbgigmap.com` is also
free, and `melbgigmap.com.au` is the Australian alternative.

## Step 1 — check a name

The registrar is Porkbun. The keys live in
`/Users/boscoh/p/deployvm/.env`. `rdomain` reads that file from any
directory.

Check a name:

```bash
rdomain check melbgigmap.com
```

The answer is `AVAILABLE` or `TAKEN`, and the price for one year. Porkbun
limits the check to 10 calls for each 10 seconds. Wait one second between
calls.

Check many names:

```bash
for d in melbgigmap.com melbgigmap.com.au; do rdomain check "$d"; sleep 1; done
```

The raw API call, for reference:

```bash
cd /Users/boscoh/p/deployvm
set -a; . ./.env; set +a
curl -s -X POST \
  "https://api.porkbun.com/api/json/v3/domain/checkDomain/melbgigmap.com" \
  -H 'Content-Type: application/json' \
  -d "{\"apikey\":\"$PORKBUN_API_KEY\",\"secretapikey\":\"$PORKBUN_SECRET_KEY\"}"
```

The answer holds `response.avail` (`yes` or `no`) and `response.price` in USD.

## Step 2 — register the name

### Verify the account first

Porkbun answers `VERIFICATION_REQUIRED` on `domain/create` while the account
email address or phone number is not verified. Both are verified now, so the
registration on 2026-10-12 succeeded.

Verify both on the web page at `https://porkbun.com/account`. The same work is
possible over the API:

- `POST /account/verifyEmail/send` sends a code. `POST
  /account/verifyEmail/confirm` with `{"code": "..."}` confirms it. The link
  in the email works too.
- `POST /account/verifyPhone/send` texts a code. `POST
  /account/verifyPhone/confirm` with `{"code": "..."}` confirms it.

### Add credit first

API purchases come from prepaid account credit. A purchase never charges a
saved card by itself. The account held credit on 2026-10-12, so the purchase
succeeded. A dry run against an empty balance answers `wouldSucceed: false`.

Add credit at `https://porkbun.com/account/credit`. Turn on auto top-up at
`https://porkbun.com/account/api` to prevent the block later.

You can also pay one purchase directly with `payWith: "card"` or
`payWith: "usdc"` on `domain/create`. `rdomain` does not send that field.

`rdomain register` charges the account. Run `rdomain check` first.

```bash
rdomain register gigmap.dev
```

The command checks the name again, then calls `domain/create` with the price
in cents. It prints the order ID and the account balance in cents.
Registration auto-renews.

The run on 2026-10-12 gave order `12082816` for `gigmap.dev` at $8.75 for one
year. The balance after the purchase was $21.25.

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

## Steps that deployvm and rdomain miss

This list records the work that no command does. Each item names the tool gap
and the manual step that fills it. The list comes from the `gigmap.dev` setup
on 2026-10-12.

### The name

- **Account verification.** Porkbun answers `VERIFICATION_REQUIRED` on
  `domain/create` until the account email address and phone number are
  verified. `rdomain` has no command for this. Use the web page at
  `https://porkbun.com/account`, or the raw `account/verifyEmail` and
  `account/verifyPhone` calls.
- **Prepaid credit.** `rdomain` cannot add credit, and it sends no `payWith`
  field. An API purchase uses account credit only. Add credit at
  `https://porkbun.com/account/credit`.
- **No dry run.** `register` charges the account. There is no `--dry-run`
  flag. Call `rdomain check` first.
- **Four commands only.** `rdomain` has `check`, `register`, `list`, and
  `nameservers`. It cannot renew, transfer, or delete a name. It cannot read
  the balance or the current nameservers.
- **The zone (fixed).** `deployvm dns <domain> --target <instance>` creates
  the zone and the `@` and `www` A records, then prints the nameservers. Run
  it before the deploy, so the A record is already in place.
- **Order matters.** Create the zone before the registrar nameserver change
  takes effect. The DigitalOcean nameservers answer `SERVFAIL` for a zone
  that is not there yet.
- **The wait (fixed).** `wait_for_dns` waits for the `A` record, and
  `--dns-timeout` sets the budget (default 300 seconds). No command waits for
  the registrar nameservers to change. The change took about 30 seconds here.
  Check it with `dig +short A gigmap.dev @8.8.8.8`.
- **The provider (fixed).** `deployvm nameservers` and `deployvm dns` read the
  provider from `--instance` or `--target`. Pass one of them: `.env` sets
  `DEPLOY_VM_PROVIDER=aws`, and a bare `deployvm nameservers <domain>` from
  this directory creates a Route 53 zone.
- **A nameserver mismatch stops the run (fixed).** `ensure_dns_matches` in
  `deployvm/server.py` warned, then continued, and certbot failed at the end.
  It now stops with the two nameserver sets, unless `--skip-dns` is set.

### The SSL

- **The old IP-only site stays (fixed).** `setup_nginx_ssl` removed the
  earlier catch-all sites with `grep -rl 'server_name _'
  /etc/nginx/sites-enabled/`. `grep -r` skips symbolic links, and the entries
  in `sites-enabled` are symbolic links, so the loop removed nothing. The
  port-8080 site `/etc/nginx/sites-enabled/gigmap` stayed after the switch.
  `remove_catch_all_sites_command()` in `deployvm/server.py` now names each
  link for `grep`. The site stays until the next SSL deploy.
- **The old port (fixed).** A deploy that moves an app from an IP-only port
  to a domain removes the old nginx site and runs
  `ufw delete allow <port>/tcp`. For a server that moved before this fix,
  close the port by hand:

  ```bash
  ssh deploy@209.38.23.184 'sudo ufw delete allow 8080/tcp'
  ```

- **`DOMAIN` in `deploy.sh` is manual.** The script does not read the domain
  from the instance file.
- **The second instance file is manual.**
  `/Users/boscoh/p/nimago/tspots.instance.json` holds the live record. The
  copy at `/Users/boscoh/p/deployvm/tspots.instance.json` is stale. deployvm
  does not keep the two in step.
- **`www` is implicit.** certbot asks for `<domain>` and `www.<domain>`
  together. `setup_dns` creates both `A` records. A registrar or a provider
  that behaves in another way gets no warning.

## Notes

- The source of `deployvm` is `/Users/boscoh/p/deployvm`. `~/.local/bin/rdomain`
  and `~/.local/bin/deployvm` are editable installs of that source. `rdomain
  --version` shows stale metadata.
- `rdomain` reads `/Users/boscoh/p/deployvm/.env` from any directory. You can
  also export `PORKBUN_API_KEY` and `PORKBUN_SECRET_KEY` in the shell.
- A subdomain of a name that is already on DigitalOcean needs no registrar
  change. Add the `A` records in the existing zone. This is the case for
  `gig.tangiblespots.com` in `docs/deploy.md`.
- `rdomain check` and `rdomain register` called the endpoint `domain/check/<name>`
  and read `avail` and `price` at the top level. The endpoint is
  `domain/checkDomain/<name>`, and the API nests the fields under `response`.
  `register` also sent `cost` in dollars instead of cents. These faults are
  fixed in `deployvm/rdomain.py`.
- The `list` command function shadowed the builtin `list`, so `rdomain
  nameservers` raised a `TypeError`. The function is now `list_domains` with
  the command name `list`.
