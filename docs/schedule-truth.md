# Schedule ground truth, 5 to 19 November 2026

The window is `2026-11-05` to `2026-11-19`, both ends included. Each row is one
live event whose page text states a weekly schedule. Days use `0` = Monday to
`6` = Sunday. Hours are 24 hour. `-` in a cell means the text states nothing.

| source | event title | url | raw schedule text (verbatim excerpt) | days | hours | stated date range | note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| artshub | Annalouise Paul: Uncharted Waters. The Journey of Mother Tongue | <https://www.artshub.com.au/event/annalouise-paul-uncharted-waters-the-journey-of-mother-tongue-2872630/> | `Exhibition Dates: 9 September – 14 November 2026, Walker Street Gallery and Arts Centre, Open 11am – 3pm, Wednesday to Saturday` | 2,3,4,5 | 11:00-15:00 | 2026-09-09 to 2026-11-14 | the hours come before the days |
| artshub | Obiectivus | <https://www.artshub.com.au/event/obiectivus-2877497/> | `Free Admission Monday to Friday, 10am – 4pm (closed public holidays)` | 0,1,2,3,4 | 10:00-16:00 | - | the text states no run |
| artshub | Between two Bays: Graeme Altmann | <https://www.artshub.com.au/event/between-two-bays-graeme-altmann-2878996/> | `Exhibition Between two Bays: Graeme Altmann is on display at Bayside Gallery from 31 October – 13 December 2026. Opening hours Bayside Gallery is open Wednesday to Friday 11am – 5pm and Saturday and Sunday 1pm – 5pm.` | 2,3,4,5,6 | 11:00-17:00 | 2026-10-31 to 2026-12-13 | second hours group: Sat-Sun 13:00-17:00 |
| artshub | Tasting notes: Evie Cahir | <https://www.artshub.com.au/event/tasting-notes-evie-cahir-2878990/> | `Exhibition Tasting notes: Evie Cahir is on display at Bayside Gallery from 31 October – 13 December 2026. Opening hours Bayside Gallery is open Wednesday to Friday 11am – 5pm and Saturday and Sunday 1pm – 5pm.` | 2,3,4,5,6 | 11:00-17:00 | 2026-10-31 to 2026-12-13 | second hours group: Sat-Sun 13:00-17:00 |
| artshub | Screening: Nicholas Currie, ‘Rodney Currie Stars in a Movie’ | <https://www.artshub.com.au/event/screening-nicholas-currie-rodney-currie-stars-in-a-movie-2878283/> | `Screening continues until Saturday 21 November Wednesday – Saturday, 11am – 4pm` | 2,3,4,5 | 11:00-16:00 | - | the text gives an end only: until 2026-11-21 |
| artshub | Qigong and Walking Meditation | <https://www.artshub.com.au/event/qigong-and-walking-meditation-2822592/> | `Dates Every Wednesday 14 October- 18 November \| 11:15am- 12:15pm` | 2 | 11:15-12:15 | 2026-10-14 to 2026-11-18 | one day of the week, stated as `every Wednesday` |

The machine form is `docs/schedule-truth.json`. It holds the same rows plus a
`reference` key, the `Event Starts` date of the page. Each row is one event, so
a row with two hours groups states its second group in the note.

## Method and counts

I read the ArtsHub feed `https://www.artshub.com.au/events/feed/` with
`location=melbourne` and `paged=1`. Page 1 held 70 items and page 2 answered
404, so the feed is one page. `parse_feed` kept the 41 events whose run overlaps
the window, and `parse_event_page` gave the text of `div.the-content` and the
`Event Starts` date of each one. 21 of the 41 texts hold a weekday word. 6 of
the 41 state a weekly schedule, and they are the rows above; the other 15 name a
weekday only inside a one-off, such as an opening reception.

For What's On I read `/search` with `scs[]=art` and `scs[]=exhibition` for the
window. That gave 70 event pages. The page text is the JSON-LD `description`
plus `div.description-container`. 20 of the 70 texts hold a weekday word, and
**0 of the 70 state a weekly schedule**. Every one of them names a weekday only
inside a one-off, for example `Join for the opening reception on Saturday 14
November, 2pm-4pm`. The real schedule of a What's On event sits in the visible
`Hours` table, one row for each date (`Thu 5 Nov | 10am - 6pm`). That table is
not prose: `whatson.parse_sessions` reads it as structure. A date list is also
the same shape as a list of one-off sessions, so a text reader cannot tell the
two apart and must not guess. So the What's On side gives no row.

Total: 6 rows. I read 41 + 70 = 111 events; 21 + 20 = 41 texts hold a weekday
word, and 6 of them state a weekly schedule.

## Mismatch against `extract_schedule`, before the change

Each row ran through `extract_schedule(text, reference)` with the `Event Starts`
date as the reference. 2 of the 6 rows are wrong.

| row | expected | got |
| --- | --- | --- |
| Annalouise Paul: Uncharted Waters | days `[2,3,4,5]`, hours `[11:00,15:00]` | days `[]`, hours `[]` |
| Qigong and Walking Meditation | days `[2]`, hours `[11:15,12:15]` | days `[]`, hours `[]` |

Both rows lose the days and the hours. The dates of all 6 rows are correct.

## After the change

Two forms came out of the two rows: a time that comes before its days (`11am –
3pm, Wednesday to Saturday`), and a single day with `every` before it
(`Every Wednesday`) that a following date must not reset. 0 of the 6 rows are
wrong now, and the other four rows do not move. I ran the new function over all
41 ArtsHub texts and all 70 What's On texts as well: it returns hours for these
6 rows only, so the new forms add no wrong answer on a one-off text.

Mismatch count: 2 of 6 before, 0 of 6 after.

## Forms it still does not read

| form | example text | why it stays empty |
| --- | --- | --- |
| a table of dates, not prose | `Hours \| Date \| Times \| Thu 5 Nov \| 10am - 6pm \| Fri 6 Nov \| 10am - 6pm` | the shape is the same as a list of one-off sessions, so a reader cannot tell them apart |
| an end with no start | `Screening continues until Saturday 21 November` | the module reports a range only, and one date is not a range |
| a list of one-off shows | `Shows: Thursday 19 November 7.00pm (preview) Friday 20 November 6.00pm` | each date names one show, not a weekly day |
| a time, then `on` and a day | `Open 10am-5pm on Wednesday` | the word `on` does not join a time to the days after it |
| a vague run | `The Story of the Moving Image is open 364 days a year` | it gives no day of the week and no time |
