"""One gig source that merges the gigs of several sources.

The server holds the `GigSource` protocol only, so the merge needs no
change in the page. The page reads one gig list, and every gig keeps the
urls of its own site.

The gig list is the lists of all members, read at the same time. A member
that fails is logged and left out, so one blocked site does not empty the
map. The read fails only when every member fails.

A gig holds a detail url and a venue url, and each url belongs to one
member. The merge notes the owner of each url when it reads the gigs. A
later `gig_detail` or `venue` read goes to that owner. The owner map lives
in the object, so one object serves one request.

The merge does not remove a repeat. Two sites that list one show give two
gigs.
"""

import asyncio
import logging

from gigmap import sources
from gigmap.logger import log_kv

LOGGER = logging.getLogger(__name__)


class CombinedSource:
    """Merge the gigs of the members. Implement `GigSource`."""

    def __init__(self, members: dict[str, sources.GigSource]) -> None:
        self._members = members
        self._owner: dict[str, sources.GigSource] = {}

    async def gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        results = await asyncio.gather(
            *(member.gigs(start_date, end_date) for member in self._members.values()),
            return_exceptions=True,
        )
        merged: list[sources.Gig] = []
        failed: list[str] = []
        for (name, member), result in zip(self._members.items(), results):
            if isinstance(result, BaseException):
                failed.append(name)
                log_kv(LOGGER.warning, "source failed", source=name, error=result)
                continue
            for gig in result:
                for url in (gig.detail_url, gig.venue_url):
                    if url:
                        self._owner[url] = member
            merged.extend(result)
        if failed and len(failed) == len(self._members):
            raise sources.SourceError("no source gave a gig list")
        return merged

    async def gig_detail(self, detail_url: str) -> sources.GigDetail | None:
        owner = self._owner.get(detail_url)
        return await owner.gig_detail(detail_url) if owner else None

    async def venue(self, venue_url: str) -> sources.Venue:
        owner = self._owner.get(venue_url)
        if owner is None:
            raise sources.SourceUrlError("no source owns this venue")
        return await owner.venue(venue_url)
