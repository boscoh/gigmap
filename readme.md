# The Melbourne Gig Map

The Beat gig guide publishes the Melbourne gigs as cards, one group for each
day. The guide cannot sort the gigs by start time. It cannot show the venues on
a map. This app turns that guide into one page: a table that sorts, and a street
map beside the table.

The tool is personal. One person uses it to choose a night out. Most decisions
below follow from that one fact: the smallest design that does the job, and no
more.

## Why a server stands in the middle

A browser page cannot read `beat.com.au` by itself.

- A page can read a response from another origin only when that site permits it.
  `beat.com.au` does not permit it.
- Cloudflare on `beat.com.au` reads the TLS fingerprint. The app uses
  `curl_cffi` to copy the fingerprint of a real browser. A page cannot do this
  for a site that it does not own.
- The geocoder needs an application name in the `User-Agent`, and the policy
  forbids a request from a browser. See the policy section.
- The parse and the saved files must live where a browser reload cannot destroy
  them.

So the browser talks to a small FastAPI server. The server alone talks to the
site, to the geocoder, and to the disk. The browser holds the view only.

## Why the start times arrive after the gigs

The guide page shows the artist, the venue, the price, and the ticket link. It
does **not** show the start time. The start time is on the detail page of each
gig. Therefore one date range costs one read for each gig in that range.

That fact shapes the whole interface.

- `GET /api/gigs` returns the list at once. The table can draw immediately, and
  the artist, the venue, and the price are all correct.
- `GET /api/times` streams one line for each gig as each start time arrives. The
  order of the answers is the order of the reads, not the order of the gigs.
- The page draws placeholder rows first. Then each line fills one row and moves
  one counter.

An empty table is worse than a full table with no times. That trade is the
reason for the two endpoints, and the reason for the stream.

The reads use a pool of five connections. Five hides the delay of a busy night.
A larger pool would look like an attack on a donated site.

## Why the store is a set of JSON files

A database is the usual answer. This app uses JSON files on disk instead, for
four reasons.

1. **The data is small.** One city, a few hundred gigs for one week. The whole
   store is smaller than one table in a database.
2. **A person must be able to read the store.** The site markup is the only
   source, and the markup breaks. When a field goes wrong, a person opens one
   file and compares it with the page. A database needs a client and a query
   first.
3. **The policy demands a cache.** The Nominatim policy requires the app to
   store each result and to not ask again. The store is a licence condition, not
   an optimization.
4. **The app is the only writer.** One process writes the files. So the design
   needs no database server and no lock.

The app writes each file through a temporary file and then renames it. A request
that reads the file at the same time sees the old content or the new content,
never a half file.

### Why each kind has its own lifetime

| Kind | Lifetime | Reason |
| --- | --- | --- |
| day file | 3 hours | the guide changes during the day. A second click inside 3 hours must be fast. |
| gig time | no limit | a start time does not change, and a new read costs one request. |
| venue | no limit | the policy forbids a second request for a venue that has coordinates. |

A venue **without** coordinates is not stored at all. The app tries again on the
next request. A failure is not a fact, so the store holds no failure. A geocode
can fail for a short time, and the next attempt can succeed.

## Why the site sits behind an interface

Every rule about `beat.com.au` lives in `beat.py`: the URLs, the client, the
selectors, the date formats, and the parses. The server never names the site. It
uses the `GigSource` protocol and receives data objects.

Three problems drive that choice.

1. **The markup is fragile.** The guide page can change on any day. One module
   to repair is better than a repair in every route.
2. **A test must not touch the network.** The test replaces the source with a
   fake through `dependency_overrides`. The routes, the stores, and the cleanup
   then run against known data.
3. **A second source is possible.** The protocol returns data, not pages. A new
   site needs a new module, and no change to the server.

The stores wrap the source in the same way. `CachedSource` obeys the protocol
and adds the disk files. So a route sees one object, and that object hides both
the site and the disk.

The parser is loose on purpose. A missing field becomes `None`, and the parse
does not raise. A strict parser would drop a whole day for one unusual card.

## Why the page is one file

`index.html` holds the whole client: the markup, the styles, and the Vue code.
The server reads the file on each request and fills two values: the source title
and the source details.

The reason is the speed of one change. An edit to the page needs a browser
refresh only. A restart is not necessary. There is no build step, no bundler,
and no `node_modules`. Vue, Bootstrap, Pug, Lodash, Ky, and Leaflet come from
public CDNs. The deploy copies the source and stops.

That choice has a price.

- The page needs those CDNs at load time.
- The client lives inside the document. So the document must stay short-lived in
  any cache. A browser with an old copy holds an old client, and no asset
  version number can repair that.

For a personal tool the trade is good. A second server or a build step would
cost more than it saves.

## Why the map uses Leaflet

The OpenStreetMap embed page shows one marker. The table holds one row for each
gig, and a busy night has many venues. So the app needs a map library that can
hold many markers. Leaflet is small and needs no key.

The tiles come from the standard OpenStreetMap service. A CSS filter on the tile
pane softens the tiles, so the map reads as a background for the table. The
filter is on the pane and not on the map, so the markers and the popups keep
their colour.

## Why the cleanup runs inside the app

`data/gigs/` grows fastest. One date range writes one file for each gig. The app
removes the files that it cannot use again: a day file for a past range, and a
gig time older than 30 days. A venue file always stays, because the policy needs
that cache.

The server runs the cleanup at startup and then once each day. It does not use
cron.

- The app is the only writer, so the task belongs to the app.
- The data directory is on the same host.
- A cron job is a second item to deploy and to watch.
- The `gigmap clean` command stays for a manual run, with `--dry-run`.

The rules use Melbourne days. The server clock is UTC. So the deploy command
pins the timezone. A wrong timezone would remove a day file that a user can
still open.

## Why the tool must stay small

Two policies set the limit of this app. Both come from the OpenStreetMap
Foundation, and both use donated servers.

**Nominatim.** The geocoder permits one request per second. It needs a
`User-Agent` that names the app. It needs stored results. It forbids bulk
geocoding, and it forbids a connection to a search box. The app asks for one
venue at a time, holds the rate, and writes every answer to the store. A
commercial service must use a commercial geocoder.

**The tiles.** The tile service needs a valid referrer and forbids bulk
download. The app loads the tiles for the current view only. Do not add a button
that downloads an area.

The attribution on the page is a condition of both policies, not a decoration.

- Nominatim: <https://operations.osmfoundation.org/policies/nominatim/>
- Tiles: <https://operations.osmfoundation.org/policies/tiles/>
- The data licence (ODbL): <https://www.openstreetmap.org/copyright>

Those rules are the reason for the personal scope. Do not separate the geocode
part and use it for a general service.

## Run

```bash
cd ~/p/gigmap
uv run gigmap serve
```

Then open `http://127.0.0.1:8000/`.

```bash
uv run gigmap --help            # the commands
gigmap serve --help             # the options
uv run gigmap serve -r          # restart after a change to a .py file
uv run gigmap clean --dry-run   # print the cleanup plan
uv run pytest                   # the tests
uv sync                         # build the environment again
```

The watcher looks at `.py` files only. That rule is correct for the other files:

- `index.html` — the server reads this file on each request. Refresh the
  browser. A restart is not necessary.
- `data/` — the server writes these files during a request. A watch on that
  directory would cause a loop.

## Deploy

The app runs on the tspots instance on DigitalOcean, at `https://gigmap.dev`.

```bash
./deploy.sh           # deploy to the port
./deploy.sh update    # send a change
./deploy.sh --ssl     # the domain and SSL, after the A records exist
```

`docs/deploy.md` holds the record, the server layout, and the cron alternative.
`docs/cleanup.md` holds the cleanup specification. `docs/domain.md` holds the
name record.

## The files

| File | Why it exists |
| --- | --- |
| `gigmap/server.py` | the HTTP routes, the dependency, and the cleanup task |
| `gigmap/sources.py` | the protocol and the data classes, so a second site is possible |
| `gigmap/beat.py` | every rule about `beat.com.au`, and the only reader of that site |
| `gigmap/geocode.py` | the only reader of Nominatim, and the map links |
| `gigmap/stores.py` | the JSON cache, the three stores, the cleanup, and the cached source |
| `gigmap/index.html` | the whole client, in one file |
| `data/` | the saved JSON |
| `docs/` | the cleanup, the deploy, and the domain records |
| `tests/` | the pytest tests |
| `deploy.sh` | the deploy |

The page layout comes from `~/p/onepageapp`.

## Where it breaks

The app trusts the site markup. A change to a page breaks the data. The parser
returns `None` for a missing field instead of raising, so a break stays quiet.

- The gig card is the only source of the artist, the venue, the price, and the
  ticket link.
- The gig detail page is the only source of the start time. A change to that
  page gives `null` times, and the table keeps its placeholder rows.
- The venue page is the only source of the address. A change there breaks the
  map.
- A venue name alone is often not enough for the geocoder. The app tries the
  address first and then the name. Some venues still have no marker.
- Cloudflare can answer a challenge. The app then tries another browser
  fingerprint.
- Cloudflare can answer `5xx`. That is a gateway or origin fault, so the app
  retries the read. The app log holds each failed answer and the `cf-ray`.
