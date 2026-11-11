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

| Venue          | Address from the source                   | Wrong pin                         | Cause                                                                                                  |
| -------------- | ----------------------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------ |
| The Substation | `1 Market Street, Newport VIC, Australia` | zone substation, Canberra ACT     | the trailing `, Australia` became the suburb, so the name query was `Substation, Australia, Australia` |
| (same cause)   | any address that ends in `, Australia`    | Belmont, Geelong                  | the suburb fallback hardcoded `Victoria`, so it built `Australia, Victoria, Australia`                 |
| Playhouse      | `100 St Kilda Rd, Melbourne VIC 3004`     | Playhouse, Kew                    | the name query ran first, and `Melbourne` appears in the display name of every suburb                  |
| The Atrium     | `Flinders St, Melbourne 3000`             | Atrium, Hawthorn                  | the same: a name with a city, and no street number to pin it                                           |
| The Curtin     | `29 Lygon St Carlton VIC 3053`            | Curtin Avenue, Lalor              | no comma, so no suburb, so the name query was `Curtin, Melbourne, Australia`                           |
| The Open Space | `11 Acacia Pl Abbotsford VIC 3067`        | Open Space Construction, Box Hill | no comma, so no suburb, so the name query was `Open Space, Melbourne, Australia`                       |

The last four share one cause: the name answered before the address. The
address found the venue in every case, and it ran second.

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
