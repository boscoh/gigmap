"""Tests for the schedule reader. They need no network."""

from datetime import date

import pytest

from gigmap.schedule import days_text, extract_schedule, fit_dates
from gigmap.sources import Hours, Schedule


def H(days, opens, closes):
    return Hours(days=days, opens=opens, closes=closes)


ALL = [0, 1, 2, 3, 4, 5, 6]

CASES = [
    ("Free Admission Monday to Friday, 10am – 4pm (closed public holidays)",
     None, [H([0, 1, 2, 3, 4], "10:00", "16:00")], None, None),
    ("Bayside Gallery is open Wednesday to Friday 11am – 5pm and Saturday and Sunday 1pm – 5pm.",
     None, [H([2, 3, 4], "11:00", "17:00"), H([5, 6], "13:00", "17:00")], None, None),
    ("Learn stand-up comedy in Melbourne over 5-evenings from Sunday to Thursday, 6.30 pm to 9.30 pm, from January 24 to 28, 2027.",
     None, [H([0, 1, 2, 3, 6], "18:30", "21:30")], "2027-01-24", "2027-01-28"),
    ("Screening continues until Saturday 21 November Wednesday – Saturday, 11am – 4pm",
     None, [H([2, 3, 4, 5], "11:00", "16:00")], None, None),
    ("Please join us for the official opening on Saturday 14 November, 1 to 4 pm .", None, [], None, None),
    ("Join us at the opening event for Digital Fantasy on Sat 7 November 6pm – 8pm, click here for more details. Free to attend, all welcome.",
     None, [], None, None),
    ("Shows: Thursday 19 November 7.00pm (preview) Friday 20 November 6.00pm (opening night) Saturday 21 November 2.00pm (matinee) Saturday 21 November 7.00pm (closing night)",
     None, [], None, None),
    ("Drop in from 1.30pm to view the exhibition and experience Alice’s performance from 2pm. The gallery will stay open until 3.30pm.",
     None, [], None, None),
    ("Drop-in creative workshops for children will run in the adjacent workshop space from 12:30pm-2:30pm.",
     None, [], None, None),
    ("on Friday, 6 November 2026, from 6:00 pm to 8:00 pm at The Frame Shop, 131 Puckle Street,",
     None, [], None, None),
    ("Exhibition runs 3 March to 28 April 2025, open Tue-Sun 10am-5pm.",
     None, [H([1, 2, 3, 4, 5, 6], "10:00", "17:00")], "2025-03-03", "2025-04-28"),
    ("Open Tuesday to Sunday 10:00-17:00", None, [H([1, 2, 3, 4, 5, 6], "10:00", "17:00")], None, None),
    ("Open daily 10am-5pm", None, [H(ALL, "10:00", "17:00")], None, None),
    ("Mon-Fri 9am-5pm, Sat 10am-2pm",
     None, [H([0, 1, 2, 3, 4], "09:00", "17:00"), H([5], "10:00", "14:00")], None, None),
    ("Monday to Friday 10-5pm", None, [H([0, 1, 2, 3, 4], "10:00", "17:00")], None, None),
    ("Saturday 1 to 4 pm", None, [H([5], "13:00", "16:00")], None, None),
    ("Open 10am - 10am daily", None, [], None, None),
    ("Open 12am - 12am, Monday to Friday", None, [], None, None),
    ("from January 24 to 28", date(2027, 1, 1), [], "2027-01-24", "2027-01-28"),
    ("from 28 April to 3 March 2025", None, [], None, None),
    ("30 February to 2 March 2026", None, [], None, None),
    ("Exhibition Dates: 9 September – 14 November 2026, Open 11am – 3pm, Wednesday to Saturday",
     None, [H([2, 3, 4, 5], "11:00", "15:00")], "2026-09-09", "2026-11-14"),
    ("Dates Every Wednesday 14 October- 18 November | 11:15am- 12:15pm",
     date(2026, 10, 14), [H([2], "11:15", "12:15")], "2026-10-14", "2026-11-18"),
    ("Open 10am-5pm on Wednesday", None, [], None, None),
    ("11am-3pm, Wednesday 14 November, and more", None, [], None, None),
    ("Monday 25am-30pm", None, [], None, None),
    ("A night of music.", None, [], None, None),
    ("", None, [], None, None),
]


@pytest.mark.parametrize("text, reference, hours, start, end", CASES)
def test_extract_schedule(text, reference, hours, start, end):
    got = extract_schedule(text, reference)
    assert got.hours == hours
    assert got.days == sorted({d for h in hours for d in h.days})
    assert (got.date_start, got.date_end) == (start, end)


# Hours: Wednesday to Friday 11am - 5pm, Saturday 1pm - 5pm. 2026-11-05 is a Thursday.
RUN = Schedule(
    date_start="2026-10-31",
    date_end="2026-12-13",
    days=[2, 3, 4, 5],
    hours=[Hours(days=[2, 3, 4], opens="11:00", closes="17:00"), Hours(days=[5], opens="13:00", closes="17:00")],
)
FIT = [
    # One day: the hours of that day alone, because the page names the day.
    (RUN, "2026-11-05", "2026-11-05", (True, "11:00am", "11am - 5pm")),  # Thursday
    (RUN, "2026-11-07", "2026-11-07", (True, "1:00pm", "1pm - 5pm")),  # Saturday
    (RUN, "2026-11-02", "2026-11-02", (False, None, None)),  # Monday
    (RUN, "2026-11-08", "2026-11-08", (False, None, None)),  # Sunday
    (RUN, "2026-12-14", "2026-12-14", (False, None, None)),  # after the run
    # Many days: the hours text names its days.
    (
        RUN,
        "2026-11-02",
        "2026-11-05",
        (True, "11:00am", "Wed - Fri 11am - 5pm, Sat 1pm - 5pm"),
    ),  # first open date: Wednesday
    (
        RUN,
        "2026-10-01",
        "2026-12-31",
        (True, "1:00pm", "Wed - Fri 11am - 5pm, Sat 1pm - 5pm"),
    ),  # long range: starts Sat 31 Oct
    (Schedule(), "2026-11-02", "2026-11-02", (True, None, None)),  # no schedule
    (Schedule(days=[1], hours=[]), "2026-11-02", "2026-11-02", (False, None, None)),
    (
        Schedule(days=[0], hours=[Hours(days=[0], opens="00:30", closes="23:00")]),
        "2026-11-02",
        "2026-11-02",
        (True, "12:30am", "12:30am - 11pm"),
    ),
    (
        Schedule(days=ALL, hours=[H(ALL, "10:00", "17:00")]),
        "2026-11-02",
        "2026-11-04",
        (True, "10:00am", "daily 10am - 5pm"),
    ),
    # One weekday with two spans, such as a late night on Friday.
    (
        Schedule(days=[4], hours=[H([4], "10:00", "17:00"), H([4], "18:00", "21:00")]),
        "2026-11-13",
        "2026-11-13",
        (True, "10:00am", "10am - 5pm, 6pm - 9pm"),
    ),
]


@pytest.mark.parametrize("schedule, start, end, want", FIT)
def test_fit_dates(schedule, start, end, want):
    assert fit_dates(schedule, start, end) == want


DAYS = [
    ([2, 3, 4], "Wed - Fri"),
    ([2, 3, 4, 5], "Wed - Sat"),
    ([0, 4], "Mon, Fri"),
    ([0, 1, 2, 3, 4, 5, 6], "daily"),
]


@pytest.mark.parametrize("days, want", DAYS)
def test_days_text(days, want):
    assert days_text(days) == want
