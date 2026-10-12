# The Melbourne Gig Map

A small FastAPI server. It fetches the Melbourne gig guide from
`https://beat.com.au/gig-guide/`, parses the band information, and saves it as
JSON. The page shows the gigs in a table, and the venues on a street map.

The page has two columns. The left column holds the title, the day buttons, a
month calendar, and a street map. The right column holds the gig table. On a
wide screen, drag the splitter between the columns to change the width.

On a narrow screen, the title and the data line show at the top of the left
column, beside the calendar. The day buttons are hidden. Click a day in the
calendar to load it. The calendar is smaller, the address is hidden, and the
table does not show the Venue column.

On a wide screen, the header shows the title and a link to the Beat gig guide page.

## Buttons

| Button | Date range |
| --- | --- |
| Today | today |
| Tmrw | tomorrow |
| Weekdays | one button for each day, today + 2 through today + 6. The label is two letters, for example "Mo". |
| Refresh | fetch the current date range again. The button sits at the left of the day buttons. |

Each button sends the two dates to the server. The server answers with a list
of gigs. The page shows the list in a table.

While a range loads, the table shows placeholder rows. The server sends the gig
list first. Then the start times arrive one by one. Each time lookup and each
venue lookup shows a spinner and a count.

A calendar sits to the right of the summary. It shows one month. The arrows
change the month. Click a day to load that day. A past day is not clickable.

On a wide screen, drag the splitter between the two columns to change the
column width.

## Run

```bash
cd ~/p/gigmap
uv run gigmap serve
```

Run `uv run gigmap` alone to see the commands.

Then open `http://127.0.0.1:8000/`.

The server is a console script. The name comes from `pyproject.toml`.

```bash
gigmap serve --help
```

| Option | Default | Purpose |
| --- | --- | --- |
| `--host` | `127.0.0.1` | address to listen on |
| `--port` | `8000` | port to listen on |
| `--data-dir` | `./data` | directory for the saved files |
| `-r`, `--reload` | off | restart the server after a change to a `.py` file |

To make the environment again, run `uv sync`.

## Hot reload

```bash
uv run gigmap serve -r
```

The server restarts after each change to a `.py` file. The command works from
any directory, because the watcher watches the project directory.

The watcher looks at `.py` files only. This is correct for the other files:

- `index.html` — the server reads this file on each request. Refresh the
  browser. A restart is not necessary.
- `data/` — the server writes these files during a request. A restart would
  cause a loop. The watcher ignores them.

## The table

| Column | Content |
| --- | --- |
| Time | the start time in black, for example "6:30pm". Small muted text below it shows the day and the month, for example "10 Oct". An end date follows after an arrow |
| Artist | the band name. A link icon after the name opens the gig page on beat.com.au. A search icon opens a web search for the artist, the categories, and the genres. Small muted text below the name shows the categories and the genres, separated by a slash. The icons sit at the right edge of the cell |
| Venue | the venue name, and a link icon to the venue page. The icon sits at the right edge of the cell |
| Ticket | a button. The text is the price. The link goes to the ticket shop |

Click a row to select the gig. The map moves to that venue. Click a map marker
to highlight the rows for that venue and to scroll to the first one.

Click the Time, Artist, Venue, or Ticket header to sort. Click the header again
to reverse the order. The table starts sorted by
Time, from early to late.


## The map

The left column holds a street map. The map shows one marker
for each venue in the current date range.
The marker popup lists the acts in time order, with the start time and the
artist in two aligned columns. Then "Venue" appears in the left column. The
venue name and the address sit in the right column, as plain text.

- Click a row in the table — the map moves to that venue.
- Click a marker — the table marks the rows for that venue and scrolls to the
  first one. On a narrow screen the row moves to the top of the table area,
  below the header. The table area is a box that scrolls on its own, so the
  page moves as well when the top of the box is above the screen.

The server reads the address from the venue page on `beat.com.au`. The server
then looks up the coordinates. A venue without a result stays in the store
without coordinates, and the map has no marker for it.

The map uses Leaflet. The OpenStreetMap embed page shows one marker only, so a
map with many markers needs a slippy map library.

The tiles come from the OpenStreetMap standard tile service at
`tile.openstreetmap.org`. The map shows the OpenStreetMap attribution. The
tile usage policy applies: a valid referrer, no bulk download, and no heavy
traffic. See <https://operations.osmfoundation.org/policies/tiles/>.
The tiles load for the current view only. Do not add a button that downloads
an area.

A CSS filter on the tile pane softens the tiles, so the map reads as a
background for the table. The filter is on the pane, not on the map, so the
markers and the popups keep their colour.

## Nominatim usage policy — read this

The coordinates come from the **Nominatim** service of the OpenStreetMap
Foundation, at `nominatim.openstreetmap.org`.

The policy is at
<https://operations.osmfoundation.org/policies/nominatim/>. The restrictions
are:

- **Maximum 1 request per second.** The server waits between requests.
- **A User-Agent that names the application is necessary.** The server sends
  `gigmap/0.1 (personal Melbourne gig guide)`. Change this string to your
  own application name before you use this code.
- **The results must be cached.** The server writes each result to
  `data/venues/<name>.json`. It uses that file from then on and never asks
  for a venue that has coordinates.
- **Attribution is necessary.** The page shows "Coordinates © OpenStreetMap
  contributors, via Nominatim" above the map, with a link to the copyright page.
- **Bulk geocoding is not permitted.** The policy permits a small one-time
  bulk task when the program uses one thread, one machine, and caches the
  results. This server obeys those rules. It asks for one venue at a time,
  holds the requests to one per second, and writes every answer to the store.
  The first load of a date range looks up its venues once. Every later load
  reads the store.
- **Automatic completion search is forbidden.** Do not connect this endpoint
  to a search box.
- The service uses donated servers. The foundation can change the rules or
  stop the service. A commercial application must use its own geocoder or a
  commercial provider.

The server is a deliberate, personal tool. Do not copy the geocoding part into
a general-purpose map or address service.

The data from OpenStreetMap is under the ODbL licence. See
<https://www.openstreetmap.org/copyright>.

## Saved files

The server writes files for each date range, one file for each venue, and one
file for each gig detail page:

```text
data/days/gigmap_2026-10-11_2026-10-11.json
data/venues/brunswick-ballroom.json
data/gigs/stella-lefty.json
```

The server uses the saved JSON for a date range when the file is
younger than 3 hours. Then a second click is fast. After 3 hours the
server fetches the page again.

A venue file is the persistent store of the venue name, the address, and the
coordinates. The server uses a stored venue without asking again. A venue file
**without** coordinates is looked up again after 30 days, because a lookup can
fail for a short time.

A gig file is the persistent store of the start time. The server reads the time
from the gig detail page one time and uses the stored value after that. The
server uses a small pool of connections for the first load of a date range.

The Refresh button adds `refresh=true`. This parameter ignores the saved range
JSON and the saved gig time files.

Set the directory with the `--data-dir` option or with the environment variable
`GIGMAP_DATA_DIR`.

## Data cleanup

The server writes files to `data/` and does not remove them. The `clean`
command removes the files that the app cannot use again.

```bash
uv run gigmap clean --dry-run   # print the plan
uv run gigmap clean             # remove
```

The command removes a day file when its range ends before today, and a gig
time file older than 30 days. It never removes a venue file, because the
Nominatim policy needs the cache.

| Option | Default | Purpose |
| --- | --- | --- |
| `--data-dir` | `./data` | directory for the saved files |
| `--gig-days` | `30` | keep a gig time file for this many days |
| `--dry-run` | off | print the plan and remove nothing |
| `--quiet` | off | print nothing |

The server runs the cleanup itself: one time at startup, then each day. The
deploy command pins the timezone, because the server is UTC. See
`docs/cleanup.md` for the cron alternative.

The full specification is in `docs/cleanup.md`.

## Deploy

The app runs on the tspots instance on DigitalOcean, at `https://gigmap.dev`.

```bash
./deploy.sh           # deploy to the port
./deploy.sh update    # send a change
./deploy.sh --ssl     # the domain and SSL, after the A records exist
```

The full record, the server layout, and the cron job are in `docs/deploy.md`.

## The JSON data

`GET /api/gigs` answers with a list of objects:

```json
{
  "artist": "Stella Lefty",
  "detail_url": "https://beat.com.au/stella-lefty/",
  "date": "2026-10-11",
  "end_date": null,
  "time": "8:00pm",
  "venue": "Prince Bandroom",
  "venue_url": "https://beat.com.au/directory/prince-bandroom/",
  "genres": ["Indie"],
  "categories": ["Music"],
  "price": "$61.10",
  "ticket_url": "https://tickets.avclive.com.au/outlet/event/2b81a8e1-0a66-4abd-932b-1c2c6179ce17",
  "image_url": "https://beat.com.au/wp-content/uploads/2026/10/Stella-Lefty1-300x225.jpg",
  "image_alt": "Stella Lefty"
}
```

The table shows the genres and the categories as small muted text below the
artist name, but the JSON keeps them in two lists. The JSON keeps the full
information.

A scalar field is `null` when the site does not show the value. A list field is
`[]`. The gig guide page does not publish a start time, support acts, a
description, or an address. The server reads the start time from each gig detail
page. `GET /api/gigs` answers with `time: null`. `GET /api/times` fills the
field. The field stays `null` when the lookup fails.

`GET /api/venue` answers with the venue data:

```json
{
  "name": "Paris Cat Jazz Club",
  "address": "6 Goldie Pl Melbourne VIC 3000",
  "venue_url": "https://beat.com.au/directory/paris-cat-jazz-club/",
  "latitude": -37.813514,
  "longitude": 144.96084,
  "display_name": "6, Goldie Place, Melbourne, Victoria, 3000, Australia",
  "map_embed_url": "https://www.openstreetmap.org/export/embed.html?bbox=…",
  "map_url": "https://www.openstreetmap.org/?mlat=…"
}
```

The `latitude`, `longitude`, and `map_embed_url` fields are `null` when the
lookup fails.

## Endpoints

- `GET /` — returns `index.html`
- `GET /api/gigs?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&refresh=false` —
  returns the list of gigs as JSON
- `GET /api/times?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&refresh=false` —
  streams one JSON line for each gig as the server reads its start time. Each
  line is `{"index": N, "time": "8:00pm"}`. `index` points into the list from
  `GET /api/gigs`
- `GET /api/venue?venue_url=<beat directory url>` — returns the venue address
  and the map links

## The source

`sources.py` declares the `GigSource` protocol and the Pydantic data
classes `Gig`, `GigDetail`, and `Venue`. The protocol returns data, not
pages. It does not name a site:

```python
class GigSource(Protocol):
    async def page(self, start_date: str, end_date: str) -> str
    async def gigs(self, start_date: str, end_date: str) -> list[Gig]
    async def gig_detail(self, detail_url: str) -> GigDetail | None
    async def venue(self, venue_url: str) -> Venue
```

`beat.py` holds `BeatSource`. This class is the only reader of
`beat.com.au`. It implements the protocol.

`sources.SourceError` means that the source cannot answer. The server
answers `502`. `sources.SourceUrlError` means that the source rejects the
url. The server answers `400`.

`stores.py` declares `DataStores` and `CachedSource`. `DataStores` builds the
three stores from the data directory and holds the cleanup of that directory.
`CachedSource` implements `GigSource` and wraps another source with those
stores.

The FastAPI dependency `source_dependency` gives one source for each
request, then closes it. The dependency builds `CachedSource` around
`BeatSource` and passes `data_dir`. It reads the
`refresh` query parameter and gives the value to the wrapper. A test
replaces the source with a fake:

```python
server.app.dependency_overrides[server.source_dependency] = lambda: fake
```

## The cache

`stores.py` holds the reusable disk cache. `CacheEntry` describes one cache
kind as data: the subdirectory, the value type, the file name, the
freshness rule, the validity rule, and the retention rule.
`JsonCache` does the shared work: it builds the path, validates on read,
writes through a temporary file, and evicts the old files.

`JsonCache.fetch` runs one cached read: load the value, or call the origin,
and store the result when it is valid. A value that fails the validity rule
is returned but not stored.

`DataStores` builds one `JsonCache` for each data kind:

| Cache | Directory | Freshness | Cleanup |
| --- | --- | --- | --- |
| `DAYS` | `data/days/` | 3 hours | a date range before today |
| `GIGS` | `data/gigs/` | never stale | older than `GIG_TTL_DAYS` |
| `VENUES` | `data/venues/` | only with the coordinates | never |

`stores.CachedSource` reads and writes the three `DataStores` stores. The
calls `geocode.enrich_venue`, so the value holds the geocode and the map
links, and the `VENUES` entry has the `valid` rule, so the wrapper stores
the venue only when it has the coordinates. The `/api/venue` route is then
one line.

`geocode.py` holds the only code that talks to nominatim.openstreetmap.org.
It enforces one request per second. The geocode is the most costly read,
so the venue is the store that matters most.

## Tests

```bash
uv run pytest
```

`tests/test_cleanup.py` covers the cleanup rules, the report, the loop that
repeats, and the lifespan that starts and stops the loop.

`tests/test_source.py` covers the `GigSource` protocol, the injected
source, and the routes.

## Files

- `gigmap/index.html` — one page Vue 3 application. Vue, Bootstrap, Pug,
  Lodash, and Ky come from CDN. The layout comes from `~/p/onepageapp`.
- `gigmap/server.py` — the FastAPI server, the HTTP routes, and the cleanup
- `gigmap/stores.py` — `JsonCache` (the reusable JSON disk cache), `DataStores`
  (the three cache kinds and the cleanup), and `CachedSource`
- `gigmap/geocode.py` — the Nominatim lookup and the map links
- `gigmap/sources.py` — the `GigSource` protocol, the data classes, and the errors
- `gigmap/beat.py` — the beat.com.au rules: the URLs, the parsers, and `BeatSource`
- `pyproject.toml` — the packages and the console script
- `pyrightconfig.json` — points Pyright at the `.venv` directory
- `data/` — the saved JSON data, the venue data, and the saved gig times
- `docs/cleanup.md` — the data cleanup specification
- `deploy.sh` — deploy the app to the tspots instance
- `docs/deploy.md` — the deployment record
- `docs/domain.md` — the domain name record
- `tests/` — the pytest tests

## Limits

- The card markup is the only source. A change to the page markup breaks the
  parser. The parser returns `null` for a missing field and does not raise.
- The gig detail page is the only source of the start time. A change to that
  markup breaks the time. The field is `null` when the lookup fails.
- The venue page markup is the only source of the address. A change there
  breaks the address lookup.
- The venue name alone is often not enough for the geocoder. The server tries
  the address first, then the name. Some venues still have no map.
- The guide page loads its own styles and scripts from `beat.com.au`. The
  parser reads only the markup, so those files do not change the data.
