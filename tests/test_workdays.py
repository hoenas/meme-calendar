from datetime import date

from memecal.workdays import (
    door_date,
    is_workday,
    total_doors,
    unlocked_count,
    workdays_between,
)


def test_wochenende_ist_kein_arbeitstag():
    assert not is_workday(date(2026, 9, 5))  # Samstag
    assert not is_workday(date(2026, 9, 6))  # Sonntag
    assert is_workday(date(2026, 9, 7))      # Montag


def test_bw_feiertage_zaehlen_nicht():
    # Heilige Drei Könige ist in BW Feiertag, in NRW nicht.
    assert not is_workday(date(2027, 1, 6), subdiv="BW")
    assert is_workday(date(2027, 1, 6), subdiv="NW")


def test_tag_der_deutschen_einheit_ueberall():
    assert not is_workday(date(2025, 10, 3), subdiv="BW")


def test_workdays_between_inklusive_grenzen():
    days = workdays_between(date(2026, 9, 7), date(2026, 9, 11))
    assert days == [
        date(2026, 9, 7),
        date(2026, 9, 8),
        date(2026, 9, 9),
        date(2026, 9, 10),
        date(2026, 9, 11),
    ]


def test_workdays_between_leer_wenn_ende_vor_start():
    assert workdays_between(date(2026, 9, 10), date(2026, 9, 1)) == []


def test_unlocked_count_waechst_mit_den_tagen():
    start = date(2026, 9, 7)   # Montag
    end = date(2026, 9, 30)
    assert unlocked_count(start, start, end) == 1
    assert unlocked_count(start, date(2026, 9, 11), end) == 5
    # Wochenende schaltet nichts frei.
    assert unlocked_count(start, date(2026, 9, 13), end) == 5


def test_unlocked_count_deckelt_auf_gesamtzahl():
    start = date(2026, 9, 7)
    end = date(2026, 9, 11)
    total = total_doors(start, end)
    assert unlocked_count(start, date(2027, 1, 1), end) == total


def test_unlocked_count_null_vor_start():
    assert unlocked_count(date(2026, 9, 7), date(2026, 9, 1), date(2026, 9, 30)) == 0


def test_door_date_ist_eins_basiert():
    start = date(2026, 9, 7)
    end = date(2026, 9, 30)
    assert door_date(start, 1, end) == date(2026, 9, 7)
    assert door_date(start, 5, end) == date(2026, 9, 11)
    assert door_date(start, 999, end) is None
