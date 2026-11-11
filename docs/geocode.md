# The venue address and its coordinates

## The purpose

`gigmap/geocode.py` reads Nominatim and returns one point for each venue.
A wrong point is worse than no point: the reader sees a pin in the wrong
city and trusts it. This page records the wrong points that the app has
served, and the rule that each one changed.

## The lookup order

`geocode_venue` asks in this order, and the first place wins:

| Order | Query              | Rule                                                                                        |
| ----- | ------------------ | ------------------------------------------------------------------------------------------- |
| 1     | the street address | one building, so the answer is precise                                                      |
| 2     | the venue name     | only when the address finds nothing, and only when the name can be checked against a suburb |
| 3     | the suburb centre  | a fallback, so a venue has a marker at all                                                  |

The name carries the suburb: `Playhouse, Melbourne` finds a hall in Kew,
and `Playhouse, Carlton` does not. A name answer counts only when its
`display_name` contains the suburb, because a bare name matches any place
of that name in the country.

A street without a comma yields no suburb. The name of such a venue cannot
be checked, so it does not run. `Curtin, Melbourne` is Curtin Avenue in
Lalor, and the venue is the hotel in Carlton.

## The address parts

| Part    | Rule                                                                             |
| ------- | -------------------------------------------------------------------------------- |
| suburb  | the last comma part, with the state, the postcode and a trailing country removed |
| name    | no leading `The`, no bracketed gloss, then `, <suburb>, Australia`               |
| address | the address, then `, Australia`, but only when the address lacks it              |

## The wrong pins

| Venue                   | Address from the source                       | Wrong pin                              | Cause                                                                                                  |
| ----------------------- | --------------------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| The Substation          | `1 Market Street, Newport VIC, Australia`     | zone substation, Canberra ACT          | the trailing `, Australia` became the suburb, so the name query was `Substation, Australia, Australia` |
| (same cause)            | any address that ends in `, Australia`        | Belmont, Geelong                       | the suburb fallback hardcoded `Victoria`, so it built `Australia, Victoria, Australia`                 |
| Playhouse               | `100 St Kilda Rd, Melbourne VIC 3004`         | Playhouse, Kew                         | the name query ran first, and `Melbourne` appears in the display name of every suburb                  |
| The Atrium              | `Flinders St, Melbourne 3000`                 | Atrium, Hawthorn                       | the same: a name with a city, and no street number to pin it                                           |
| The Curtin              | `29 Lygon St Carlton VIC 3053`                | Curtin Avenue, Lalor                   | no comma, so no suburb, so the name query was `Curtin, Melbourne, Australia`                           |
| The Open Space          | `11 Acacia Pl Abbotsford VIC 3067`            | Open Space Construction, Box Hill      | no comma, so no suburb, so the name query was `Open Space, Melbourne, Australia`                       |
| City Gallery, Town Hall | `110 Swanston St, Melbourne 3000`             | 110 Swanston Street, Templestowe Lower | Nominatim holds no `110 Swanston Street` in postcode 3000. The one match is a street 15 km away        |
| Shrine of Remembrance   | `Birdwood Ave, Melbourne 3001`                | Birdwood Avenue, Macleod               | the address query took the first of two answers. The second is the Shrine, in postcode 3004            |
| SOFI'S LOUNGE           | `25 Collins St, Melbourne 3000`               | 25 Collins Street, Thornbury           | Nominatim holds no `25 Collins Street` in postcode 3000. All 11 matches sit in other suburbs           |
| CAVES                   | `Rm 5 Level 8/37 Swanston St, Melbourne 3000` | Caves Way, Donnybrook                  | the room prefix defeats the address query, and the name `CAVES` then matched a street called Caves Way |

The Playhouse, Atrium, Curtin and Open Space rows share one cause: the name
answered before the address. The address found the venue in every case, and
it ran second.

The last four rows share a different cause. See below.

## The limit of the data

No query form fixes the last four. Nominatim holds no place for three of
these addresses. A free-text search of 50 results gives the table below. A
structured search (`street`, `city`, `postalcode`) gives the same places,
so the query form is not the cause.

| Address                          | Results, 50 deep | Results in the stated postcode |
| -------------------------------- | ---------------- | ------------------------------ |
| `110 Swanston Street, Melbourne` | 1                | 0                              |
| `25 Collins Street, Melbourne`   | 11               | 0                              |
| `Birdwood Avenue, Melbourne`     | 9                | 0                              |

Measured on 2026-11-10.

The city part of the query does not constrain the answer. Every street name
in greater Melbourne matches `Melbourne`, so a query for `25 Collins Street,
Melbourne 3000` returns the Collins Streets of Thornbury, Preston, Essendon
and others, and none of them is in 3000.

Two consequences:

- A rule on the answer is the only defence. An address answer must hold a
  postcode near the one that the address states. When no answer holds it,
  the address has no place, and the name or the suburb centre must answer.
- The `CAVES` row has a second fault that a query can fix. The room prefix
  `Rm 5 Level 8/` defeats the search. `37 Swanston St, Melbourne 3000`
  finds Cathedral Arcade at that address, in postcode 3000.

## The answer guard

`geocode` asks for 5 results and keeps the first one that passes its rule.

| Query         | Rule                                                                     |
| ------------- | ------------------------------------------------------------------------ |
| the street    | the postcode of the answer is within `POSTCODE_RANGE` (5) of the address |
| the name      | its `display_name` holds the suburb of the address                       |
| suburb centre | none, because it is the last fallback                                    |

`address_query` keeps only the text after the last `/` in the street part,
so a room or level prefix cannot hide the street number.

The nine reported addresses answer as below. Measured on 2026-11-11.

| Venue                   | Pin                                               |
| ----------------------- | ------------------------------------------------- |
| The Substation          | The Substation, 1 Market Street, Newport          |
| Playhouse               | Hamer Hall, 100 St Kilda Road, Southbank          |
| The Atrium              | Flinders Street, Melbourne                        |
| The Curtin              | John Curtin Hotel, 29 Lygon Street, Carlton       |
| The Open Space          | 11 Acacia Place, Abbotsford                       |
| City Gallery, Town Hall | the suburb centre, about 300 m from the Town Hall |
| Shrine of Remembrance   | Birdwood Avenue, Melbourne                        |
| SOFI'S LOUNGE           | the suburb centre, about 1 km from 25 Collins St  |
| CAVES                   | Cathedral Arcade, 37 Swanston Street              |

## The cache

A wrong answer stays on disk until the version moves.

| Constant         | Raise it when                                                                                   |
| ---------------- | ----------------------------------------------------------------------------------------------- |
| `VENUES_VERSION` | the query changes, or a venue file holds the wrong place. The old hits drop at the next cleanup |
| `MISS_VERSION`   | the query changes. An old miss says `no place` for a query that now finds one                   |

The cleanup runs at startup, then once each day. A bump of
`VENUES_VERSION` re-geocodes each venue as a reader asks for it, so the
first request after a deploy is slower.

## Check one address

```bash
.venv/bin/python -c "
import asyncio
from gigmap import geocode
place = asyncio.run(geocode.geocode_venue('The Curtin', '29 Lygon St Carlton VIC 3053'))
print(place)
"
```

The function prints the coordinates and the `display_name` of the chosen
place. Read the `display_name` before you trust the point.

## Add a case

A new wrong pin needs a test in `tests/test_geocode.py`. The test replaces
`geocode` with a stub, so it reads no network. Give the stub the answer
that the bug produced, and assert the query that should answer.
