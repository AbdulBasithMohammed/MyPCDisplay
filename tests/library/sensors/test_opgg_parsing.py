"""Parser regressions for op.gg, using the real page shapes.

Both bugs here were found on the panel rather than in a test:

1. Rune blocks label their numbers AFTER the values, so the generic stat
   parser read the PICK rate as the win rate - the screen confidently showed
   55.42% for a page whose win rate was 52.30%.
2. The tier-list rank guard demanded a perfectly consecutive sequence, so one
   unmatched champion name (Kai'Sa, whose apostrophe is typographic) silently
   truncated every row after it.

    venv/Scripts/python.exe -m pytest tests/library/sensors/test_opgg_parsing.py -q
"""
from library.sensors import opgg

# Trimmed from a live build page: two perkStyle images, a keystone, then the
# labelled stat run that fooled the generic parser.
RUNE_HTML = (
    '<div><img src="/perkStyle/8000.png" alt="Precision">'
    '<img src="/perk/8008.png" alt="Lethal Tempo">'
    '<img src="/perkStyle/8400.png" alt="Resolve">'
    '<span>55.42% 89,832 Games Pick rate 52.30% Win rate</span></div>'
)


def test_rune_stats_split_pick_from_win():
    rows = opgg._runes(RUNE_HTML)
    assert rows, "the cluster should be found at all"
    row = rows[0]
    assert row["pick"] == "55.42%", "leading number is the pick rate"
    assert row["win"] == "52.30%", "the labelled trailing number is the win rate"
    assert row["games"] == "89,832"
    assert row["keystone"] == "8008"


def test_rune_row_without_labels_still_parses():
    """Unlabelled layouts must keep working through the generic fallback."""
    html = RUNE_HTML.replace("Pick rate 52.30% Win rate", "52.30%")
    rows = opgg._runes(html)
    assert rows and rows[0]["win"] == "52.30%"


# The ranking table as plain text, with a typographic apostrophe in Kai'Sa and
# a "weak against" number after the row that must not be mistaken for a rank.
TIER_TEXT = (
    "Rank Champion Tier Role Win rate Pick rate Ban rate "
    "1 Jinx 52.25% 16.69% 5.61% "
    "2 Tristana 51.57% 9.37% 2.10% "
    "3 Kai’Sa 50.80% 12.40% 3.30% "
    "4 Miss Fortune 51.10% 7.20% 1.90% "
    "5 Jhin 50.26% 8.34% 1.20% "
)


def test_tier_rows_survive_apostrophes_and_two_word_names():
    rows = []
    seen, last = set(), 0
    for m in opgg.TIER_ROW_RE.finditer(TIER_TEXT):
        rank, name, win, pick, ban = m.groups()
        name, rank = name.strip(), int(rank)
        if name in seen or rank <= last:
            continue
        seen.add(name)
        last = rank
        rows.append(name)
    assert rows == ["Jinx", "Tristana", "Kai’Sa", "Miss Fortune", "Jhin"], rows


def test_tier_row_regex_captures_all_three_rates():
    m = opgg.TIER_ROW_RE.search(TIER_TEXT)
    assert m.group(2) == "Jinx"
    assert (m.group(3), m.group(4), m.group(5)) == ("52.25", "16.69", "5.61")
