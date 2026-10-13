# The Melbourne Gig Map

This shows how I decide what to see on a weekend.

The live site is at [gigmap.dev](https://gigmap.dev).

I've found that existing gig guides do not visualize their data the way I want.
So `gigmap` sorts gigs by days, shows where they are on a map, and lets you sort
the data on the page.

## This is a data reformatter

The data here is extracted from other websites, in this case, the excellent
Melbourne music magazine `beat.com.au`.

We then merge all the relevant information - dates, venue location, times, geo
location, Google search terms - and send it to the frontend.

The backend has been nicely refactored, so a different data source can easily be
slotted in. The data fetching has been gated and cached so it does not hammer
the origin data source. It's a small data set so we only use a simple json cache
with a cleanup sweep.

## The frontend is one single html page

Since this is a small site, I've saved myself all the hassle of compiling a
webapp by taking the [one page approach](https://github.com/boscoh/onepageapp).
There is only one major frontend framework that makes this easy: Vue. Every
external module is loaded from a CDN ([Vue](https://vuejs.org/), Bootstrap,
[Pug](https://pugjs.org/), [Leaflet](https://leafletjs.com/)).

I use [pug](https://pugjs.org/) templates instead of html, not because I am a
masochist, but because I genuinely read, and modify, pug much faster than with
html.

The mapping comes from the brilliant open-source OpenStreetMap, which is a free
service, so we must be careful in how we use it.

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

`docs/deploy.md` holds the record, the server layout, the domain, and the cron
alternative. The cleanup specification is in `gigmap/stores.py`.

## The files

| File                | Why it exists                                                        |
| ------------------- | -------------------------------------------------------------------- |
| `gigmap/server.py`  | the HTTP routes, the dependency, and the cleanup task                |
| `gigmap/sources.py` | the protocol and the data classes, so a second site is possible      |
| `gigmap/beat.py`    | every rule about `beat.com.au`, and the only reader of that site     |
| `gigmap/geocode.py` | the only reader of Nominatim, and the map links                      |
| `gigmap/stores.py`  | the JSON cache, the three stores, the cleanup, and the cached source |
| `gigmap/index.html` | the whole client, in one file                                        |
| `data/`             | the saved JSON                                                       |
| `docs/`             | the deploy and the domain record                                     |
| `tests/`            | the pytest tests                                                     |
| `deploy.sh`         | the deploy                                                           |
