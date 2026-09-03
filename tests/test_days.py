from datetime import date

from memecal.days import days_between, door_date, total_doors, unlocked_count


def test_days_between_inklusive_grenzen():
    days = days_between(date(2026, 9, 7), date(2026, 9, 11))
    assert days == [
        date(2026, 9, 7),
        date(2026, 9, 8),
        date(2026, 9, 9),
        date(2026, 9, 10),
        date(2026, 9, 11),
    ]


def test_days_between_zaehlt_wochenende_mit():
    # Montag bis Sonntag: alle 7 Tage, nicht nur die 5 Werktage.
    days = days_between(date(2026, 9, 7), date(2026, 9, 13))
    assert len(days) == 7
    assert date(2026, 9, 12) in days  # Samstag
    assert date(2026, 9, 13) in days  # Sonntag


def test_days_between_leer_wenn_ende_vor_start():
    assert days_between(date(2026, 9, 10), date(2026, 9, 1)) == []


def test_unlocked_count_waechst_mit_den_tagen():
    start = date(2026, 9, 7)   # Montag
    end = date(2026, 9, 30)
    assert unlocked_count(start, start, end) == 1
    assert unlocked_count(start, date(2026, 9, 11), end) == 5
    # Anders als bei Werktagen: das Wochenende schaltet weiter frei.
    assert unlocked_count(start, date(2026, 9, 13), end) == 7


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
