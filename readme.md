# Melb Gig Map

`gigmap` is a user interface for the way I look for events. Most event sites
do not work this way. As a casual gig goer, I want to see all the gigs for one
day, in a list and on a map. The location matters, and so does a quick scan. I'd
like to quickly see prices, do a quick search, and buy tickets.

The live site is at [gigmap.dev](https://gigmap.dev).

## This is a data reformatter

The app reads the data from other websites. The main source is the excellent
Melbourne music magazine [beat.com.au](https://beat.com.au). The second source
is the [Melbourne Fringe](https://melbournefringe.com.au).

The app merges the dates, the venue location, the times, the coordinates, and
the Google search terms. Then it sends one list to the frontend.

The sources share a protocol. The protocol lets a different data source
replace the current one without a change to the server. The server caches each
answer and limits the request rate, so the origin does not get too many
requests. The data set is small, so the cache is a set of JSON files, and a
cleanup task removes the old files.

## The frontend is one HTML file

The site is small, so it uses the
[one page approach](https://github.com/boscoh/onepageapp) and has no build
step. Vue is the one major frontend framework that makes this easy. Every
external module comes from a CDN: [Vue](https://vuejs.org/), Bootstrap,
[Pug](https://pugjs.org/), and [Leaflet](https://leafletjs.com/).

The client uses [Pug](https://pugjs.org/) templates, not HTML. I read and
change Pug much faster than HTML.

The map uses OpenStreetMap tiles. OpenStreetMap is a free service, so the app
must obey its usage policy. Nominatim allows one request each second and
needs cached results. The app makes one request each second, and it keeps the
venue cache.

## Run

The app needs Python 3.10 or later, and `uv`.

```bash
uv run gigmap serve
```

Then open `http://127.0.0.1:8000/`. The Fringe site is at
`http://127.0.0.1:8000/fringe/`.

```bash
uv run gigmap --help            # the commands
uv run gigmap serve --help      # the options
uv run gigmap serve -r          # restart after a change to a .py file
uv run gigmap clean --dry-run   # print the cleanup plan
uv run pytest                   # the tests
uv sync                         # build the environment again
```

The watcher looks at `.py` files only. That rule is correct for the other
files:

- `index.html`: the server reads this file on each request. Refresh the
  browser. You do not need a restart.
- `data/`: the server writes these files during a request. A watch on that
  directory would cause a loop.

## The sites

One server answers both sites. The path selects the site:

| Path | The site | A gig is | The reads |
| ---- | -------- | -------- | --------- |
| `/` | beat.com.au | one gig card of the guide | `/api/beat/*` |
| `/fringe/` | melbournefringe.com.au | one performance of the festival | `/api/fringe/*` |

A wrong source name answers 404, so one site never answers for another site.
Each site keeps its own cache files, so one site never answers from the cache
of the other.

## The files

| File                | Why it exists                                                         |
| ------------------- | --------------------------------------------------------------------- |
| `gigmap/server.py`  | the HTTP routes, the dependency, and the cleanup task                 |
| `gigmap/sources.py` | the protocol and the data classes, so a second site is possible       |
| `gigmap/beat.py`    | every rule about `beat.com.au`, and the only reader of that site      |
| `gigmap/fringe.py`  | every rule about the Fringe API, and the only reader of that site     |
| `gigmap/geocode.py` | the only reader of Nominatim, and the venue coordinates               |
| `gigmap/logger.py`  | the plain log form, the key/value blocks, and the request lines   |
| `gigmap/stores.py`  | the JSON cache, the three stores, the cleanup, and the cached source  |
| `gigmap/index.html` | the whole client, in one file                                         |
| `data/`             | the saved JSON                                                        |
| `tests/`            | the pytest tests                                                      |
