"""Tests for the schedule reader. They need no network."""

from datetime import date

import pytest

from gigmap.schedule import extract_schedule
from gigmap.sources import Hours


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
    ("from January 24 to 28", date(2027, 1, 1), [], "2027-01-24", "2027-01-28"),
    ("from 28 April to 3 March 2025", None, [], None, None),
    ("30 February to 2 March 2026", None, [], None, None),
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
