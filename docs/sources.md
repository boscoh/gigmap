# The gig sources

This document describes the two source modules that I added: `artshub.py` and
`artscentre.py`. It records what I explored, the method, and what I found.

The loop for each site:

1. Find the real data path. Fetch the HTML page, read the network shape, and
   try the obvious candidates: the search page, the JSON API, the RSS feed,
   and the sitemap.
2. Verify each claim against the live site. I delegated the Arts Centre
   recon to a subagent. I re-ran every load-bearing claim myself before I
   wrote code.
3. Write one module that holds every sight rule, with pure parse functions.
4. Prove it live through the running server, not only in the tests.

Both sites fight scrapers, and both needed more than one attempt to find a
working path.

# ArtsHub

Target: `https://www.artshub.com.au/event/search/?location=melbourne`

## What I explored

| Candidate | Result |
| --- | --- |
| `/event/search/?location=melbourne` | 403, `cf-mitigated: challenge`, for every fingerprint that I tried |
| `/wp-json/wp/v2/events` | 404 `rest_no_route`, so the `event` post type is not exposed |
| `/event/` archive | carousels only, and no pagination |
| `/venue/...` pages | 404, and `/venues/feed/` is empty |
| Event JSON-LD | absent, only `BreadcrumbList` |
| `/events/feed/` | 200, structured, and not challenged. This is the path that I used |

The site is WordPress behind Cloudflare, and it blocks the search page. It
does not block the WordPress RSS feed.

I tried the fingerprints `chrome`, `safari_ios`, and `firefox` on the search
page. Each one answered the challenge.

## What the feed holds

`/events/feed/` answers 200 items on page 1, 86 items on `?paged=2`, and 404
on `?paged=3`. That is 286 items in total. A request with
`?location=melbourne` answers 66 items on one page.

An item holds `title`, `link`, `startDateTime`, `endDateTime`, `venue`,
`venueAddress`, `locations`, `classifications`, `art_forms`, `pubDate`,
`guid`, and `dc:creator`.

The feed holds no image: `media:content` appears zero times. The feed holds
no price. The detail pages hold no price field and no booking link either;
`price`, `ticket`, `book`, and `buy` all give zero hits.

## The rules

- Read the feed, not the search page. No date parameter works, and the site
  ignores an unknown query key without a message. `gigs()` sends
  `location=melbourne` and then checks the `locations` tag again.
- The module always answers `time=None`. The stamp holds a start time and an
  end time, but the site does not show them as the time of the show. Most
  stamps are an arbitrary time or `00:00:00`. The module reads the day only.
- Drop an item that has no `startDateTime`. Eight items are like this, and
  such an item cannot fall in a date range.
- `gig_detail()` makes no request. The feed holds nothing more to report.
- The venue url has the form `/venue/<name>/<address>/`. `sources.url_key`
  takes the last part of the path, so the address is the key of the cache
  file. `venue()` reads the name and the address from the url and makes no
  network read.
- Use one fingerprint and do not rotate it. A `cf-mitigated` header stops
  the read, because a second try answers the same challenge.

## The result

`gigmap/artshub.py` is 363 lines, with 13 tests in `tests/test_artshub.py`.

The live server answers 26 gigs for the range 2026-11-14 to 2026-11-15. All
of them carry coordinates. For example, Kew Court House resolves to
`-37.8066944, 145.0316444`.

Two gaps, and the module docstring records both: the feed holds no price and
no ticket data, and the feed holds no image. An image costs one request for
each event.

## The schedule

The feed holds no time. `gigs` reads the event page of each gig, 5 at a time,
takes the text of `div.the-content`, and passes it to `extract_schedule`
(`gigmap/schedule.py`). The schedule is a date range, the `days` (0 = Monday)
and the `hours` (a list of `days`, `opens`, `closes`).

`fit_dates` then checks the schedule against the chosen dates:

- A gig that is closed on every chosen date leaves the list.
- For an open gig, `time` is the opening time of the first open date, for
  example `1:00pm`.
- A gig with no readable page, or no schedule, stays with no time.

The list waits for the page reads. `CachedSource` keeps the answer for the date
range. `gig_detail` makes no request.

Deploy note: `DAYS_VERSION` is now `v10`, because an old cached day list holds
no filter. The server ignores the old day files, and the clean command removes
them.

# Arts Centre Melbourne

Target: `https://www.artscentremelbourne.com.au/whats-on/event-calendar`

## What I explored

The calendar page is a React shell with no event in the HTML, and it sits
behind Imperva. The React bundle asks one JSON endpoint, and that endpoint
answers a normal client:

```
POST /api/acm/events/GetFilters
{ "StartDate": "2026-11-04", "EndDate": "2026-11-04", "TileType": 2,
  "PageNum": 1, "SortOrder": 2, "ItemsPerPage": 1000,
  "GenreId": null, "Filters": [], "PageType": 1 }
```

A window of six months answered 323 rows: 36 events, and 156 days of events.
The read took 45 seconds. A window of seven days takes about three seconds.
The user interface asks for one day, so the normal case is fast.

## Three things that the recon did not find

I checked the delegated report and found three problems in it. Each one is
now in the module.

1. The key casing is not fixed. With `Accept: application/json` the site
   answers `events` and `title`. Without that header it answers `Events` and
   `Title`. The content negotiation of ASP.NET picks the spelling. A module
   that reads one spelling stops when a proxy drops the header. The module
   reads both spellings, and I proved it live on the same window.
2. `PageNum` steps by seven days and not by page. Page 2 repeats a part of
   page 1. The module never sends a page above 1. It reads the window again
   instead.
3. A venue with the name `Various` made a false pin in Coburg. `Various` is a
   festival in more than one space, and it is not a place. The row keeps the
   name as text and holds no venue address.

## What the rows hold

The endpoint answers one row for each event and each day of its run. A run
of four nights is four rows.

The `dateTime` field is stale. The module reads `eventDateValue` and
`eventTimeValue` instead. The stamp holds the offset of the season, `+11:00`
or `+10:00`, so the hour in the stamp is the local hour. The module needs no
timezone arithmetic.

The rows of the six-month window hold these categories: Exhibition 144,
Classical Music 62, Guided Tour 48, Theatre 22, Comedy 17, Contemporary
Music 11, and smaller counts for the rest.

The rows hold 10 different venues. They hold these labels: 302 empty, then
`New Show!` 11, `Audio Described` 2, `Opening Night` 2, `Preview` 2,
`Captioned` 1, and `Auslan Interpreted` 2.

The `bookNowUrl` field holds 144 addresses at the ticketing of the site, 119
values of `"#"`, which means a booking window on the event page, 40 addresses
with `?k=FOH`, 8 with `?k=CST`, and some addresses at Ticketmaster, AXS, and
Ticketek.

The calendar holds no price, no sold-out state, no `image_alt`, no
`end_date`, and no venue link.

## The rules

| Rule | Why |
| --- | --- |
| One row becomes one gig | The rule keeps "what is on tonight" correct, and it matches `fringe.py` |
| Skip Exhibition and Guided Tour | They are 59% of the rows, one for each open day. The set `EXCLUDED_CATEGORIES` holds the rule, and one edit restores them |
| Read in windows of 170 days | The endpoint stops at 180 days from `StartDate` without a message |
| `gig_detail()` makes no request | The calendar holds the time. The price and the sold-out state sit on the event page, at one slow read for each gig |
| Report a ticket only for a real address or a real note | A "Book tickets" badge that does nothing misleads. 97 of 133 gigs hold no ticket |
| Hold the street address in `VENUE_ADDRESSES` | The calendar holds no venue link, and a venue page is slow behind Imperva. The table gives the geocoder an address without a read |

## The result

`gigmap/artscentre.py` is 472 lines, with 22 tests in
`tests/test_artscentre.py`.

The live server answers 5 gigs for 2026-11-14, and all of them carry
coordinates. The address 100 St Kilda Rd resolves to
`-37.8201575, 144.9679835`.

A range of 178 days needed two windows and answered 133 gigs with no
duplicate. The boundary day came from both windows: 122 gigs before it, and
11 after it. 131 of the 133 gigs carry a pin. A second request came from the
cache in 0.026 seconds.

# The files

| File | Change |
| --- | --- |
| `gigmap/artscentre.py` | new, 472 lines |
| `gigmap/artshub.py` | new, 363 lines |
| `tests/test_artscentre.py` | new, 22 tests |
| `tests/test_artshub.py` | new, 13 tests |
| `gigmap/server.py` | 6 lines: `SOURCE_FACTORIES` and `SOURCE_INFO` |
| `gigmap/__init__.py` | the docstring lists the new modules |
| `readme.md` | the source list, the site table, and the file table |

# The check

```bash
.venv/bin/python -m pytest tests/ -q            # 144 passed
GIGMAP_DATA_DIR=/tmp/x .venv/bin/python -m gigmap.server serve --port 8095
curl -sD- "http://127.0.0.1:8095/api/artscentre/events?start_date=2026-11-14&end_date=2026-11-14"
curl -sD- "http://127.0.0.1:8095/api/artshub/events?start_date=2026-11-14&end_date=2026-11-14"
```

Both requests answer 200 with an `x-gig-total` header, 5 and 25. Both pages
answer 200 with the correct `<title>`. `beat` answers 18 gigs for the same
day, so nothing broke.

# Two notes

Pyright reports that `artscentre` and `artshub` are unknown import symbols.
That report is wrong. The import works at run time, `gigmap` is an editable
install that points at the local directory, and all 144 tests pass. Pyright
indexed the package before the new files existed.

Two gaps are deliberate:

- The source holds no price and no sold-out state for the Arts Centre. That
  data exists only on an event page, and the read is slow.
- Four gigs at `The Leaper Family Pavilion` carry no pin. The name is not in
  OpenStreetMap, and the page of the site is behind Imperva. A missing pin is
  better than a guessed pin.

# What's On (City of Melbourne)

Target: `https://whatson.melbourne.vic.gov.au/search`

## What I explored

The site is a Rails/Turbo app behind CloudFront. It answers a plain HTTP
request with no challenge, and `robots.txt` allows `/search`. Both the search
results and the event pages are server-rendered HTML, so no JavaScript is
needed.

The search page is the list. The primary category is `c=things_to_do`, and
the three subcategories are `scs[]=theatre` for the checkbox "Theatre and
shows", `scs[]=music` for "Music, concerts, and gigs", and `scs[]=comedy`
for "Comedy". A date query uses
`dkw=specific_dates` and `date_range` as `YYYY-MM-DD to YYYY-MM-DD`, with a
literal ` to ` separator and both ends included. `q=` is required and empty.

A page holds 20 cards, and a page above the last page answers 200 with zero
cards. Measured counts with theatre and music: one day is 13 events, three days
is 37, sixty days is 168.

## What the search page holds

The date filter is coarse. It matches the run of an event, not the sessions:
a 6-8 Nov query returns events whose run window overlaps, even when no session
falls in those days. For example, The Vaudeville Revue runs 2026-08-07 to
2027-01-02 and still appears. So the search page yields candidates only, and
the module does not trust its date.

A card holds the title and the url in `a.main-link[data-gtm-variable-label]`
with `href="/things-to-do/<slug>"`, the image in `img.page_image`, the
summary in `p.summary`, and the tags in `ul.tag-list a.link--tag`. The card
also holds a `<time>` of the next session relative to today, and that date is
often outside the queried range, so the module never uses it.

## What an event page holds

`/things-to-do/<slug>` holds a JSON-LD block with `"@type": "Event"` and the
fields `name`, `description`, `image` (different on some events), `startDate`,
`endDate`, `eventStatus`, and a `location` with a name and a street address.
All 20 sampled pages had the block and a location.

`table#date-times-table` holds the sessions, one row for each session day. A
row can hold two `li` entries (for example 1pm and 7.30pm), and the module
keeps the first. Each `li` holds a `span[aria-hidden=true]` and a
`span.sr-only`; the `sr-only` text is the clean time, so the module reads it
and strips the end time.

The session day text has no year (for example `Sat 2 Jan` for a run that ends
2027-01-02), so the module takes the year from the JSON-LD run window: it
tries the year of `startDate` and the year after, and keeps the day that
falls inside `[startDate, endDate]`. The site publishes only some sessions of
a long run (Guru Dudu lists 5 of about 13), and that is the site data, not a
parse loss.

`div.price-and-bookings` holds the price and the booking. A price table holds
rows such as `Adult / general $79`; a free event holds
`<p>This is a free event</p>`. A booking link has the text `Book online`.
`div.location` holds the venue as text and a Google Maps query link, but no
coordinates.

## The rules

| Rule | Why |
| --- | --- |
| Draw one gig per session day | The search filter is coarse, so the real sessions come from the event page |
| Take the year from the JSON-LD run window | The session day text has no year |
| Send each subcategory as its own `scs[]` key of a list of pairs | A dict holds one `scs[]` key only, and the site would answer one category |
| Read the `sr-only` span and drop the end time | The `aria-hidden` span holds a display form, and one day can hold two shows |
| Build a synthetic venue url of `/venue/<name>/<address>/` | The site has no venue page, and the server geocodes the address |
| Retry a 403 once after a short sleep | The site rate-limits after about 45 reads in two minutes, then recovers |

## The result

`gigmap/whatson.py` is 627 lines, with the tests in `tests/test_whatson.py`.
The module raises one gig for each session day, with the venue address from
the JSON-LD `location`, and the ticket from the price-and-bookings box.

One gap: `image_alt` comes from the card, because the JSON-LD block holds no
alt text.

# What's On Art

Same site and same parse as What's On (`gigmap/whatson_art.py` only changes
the filter). The subcategories are `art` and `exhibition`, under
`c=things_to_do`. The page is `/whatsonart/`. It is not a member of `gig`.
