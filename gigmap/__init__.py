"""The Melbourne gig guide server.

The modules live in this package: `server` holds the HTTP routes, `sources`
holds the data interface, `artscentre` reads artscentremelbourne.com.au,
`artshub` reads artshub.com.au, `beat` reads beat.com.au, `combined` merges
the sources into one, `fringe` reads
melbournefringe.com.au, `geocode` reads Nominatim, and `stores` holds the
reusable JSON disk cache, the disk stores, and the cached source.
"""
