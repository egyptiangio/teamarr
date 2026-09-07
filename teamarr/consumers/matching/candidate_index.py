"""Inverted token index over match candidates (#747).

Every stream used to be scored against every prefetched candidate — ~2,311 of
them per stream on a profiled install, 3.36M pair visits across one sample.
#742 made each visit much cheaper but did not reduce the number of visits, and
the worst of it is invisible: 333 of that install's 424 active sources match
nothing at all, and every one of their streams still paid a full scan.

An event can only clear a ``token_set_ratio`` floor against a stream it shares
at least one word with, so a token → events index lets a stream visit only the
events that share a word. Measured: 3,360,004 visits -> 69,038 (-97.9%), for a
15.7ms build over 3,215 events.

WHAT GOES IN THE INDEX IS LOAD-BEARING
--------------------------------------
Indexing the teams' full names alone loses **8.98%** of real matches (57 of 635
measured), because abbreviation streams share no full-name token with their
event: ``ESPN UNLTD 444: NCAA Football: MORG vs. ASU`` really is
``Arizona State Sun Devils v Morgan State Bears``. Adding ``short_name`` and
``abbreviation`` takes that to **0 of 635**. Alias-expanded names were measured
too and add nothing on top, so they are deliberately not indexed.

Do not "simplify" this back to full names.

SCOPE
-----
This narrows candidates; it never decides matches. It is applied only to
TEAM_VS_TEAM streams against the shared multi-league candidate tuple, and it
yields the full list whenever it cannot speak — no tokens on the stream, or an
index that was never built. Tennis, racing and event-card streams keep the full
scan: the measurement did not cover them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from teamarr.utilities.fuzzy_match import normalize_text

if TYPE_CHECKING:
    from teamarr.core import Event

# Words that carry no identity and would otherwise drag in huge postings lists.
# "v"/"vs" alone appear in most stream names.
_STOPWORDS = frozenset({"vs", "v", "at", "the", "and", "de", "fc", "sc", "afc", "cf"})

# Minimum token length. Two-character fragments ("st", "fc", a stray initial)
# match far too much to narrow anything, and no measured stream needed one:
# 0 of 1,879 streams produced an empty token set at this floor.
_MIN_TOKEN_LEN = 3


def tokenize(text: str) -> set[str]:
    """Identity-bearing words of ``text``, normalized the way scoring sees them."""
    if not text:
        return set()
    return {
        tok
        for tok in normalize_text(text).split()
        if len(tok) >= _MIN_TOKEN_LEN and tok not in _STOPWORDS
    }


def event_tokens(event: Event) -> set[str]:
    """Every word an event could be recognised by.

    Full names, short names AND abbreviations — see the module docstring for why
    the last two are not optional.
    """
    tokens: set[str] = set()
    for side in (event.home_team, event.away_team):
        if side is None:
            continue
        tokens |= tokenize(side.name)
        tokens |= tokenize(getattr(side, "short_name", "") or "")
        tokens |= tokenize(getattr(side, "abbreviation", "") or "")
    return tokens


class CandidateTokenIndex:
    """token -> positions in a fixed candidate sequence.

    Built once per candidate set and shared by every stream in the batch, which
    is the only way it pays for itself: rebuilding per stream would cost far
    more than the scan it replaces.
    """

    __slots__ = ("_candidates", "_postings")

    def __init__(self, candidates: Sequence[tuple[str, Event]]):
        self._candidates = candidates
        postings: dict[str, list[int]] = {}
        for pos, (_league, event) in enumerate(candidates):
            for token in event_tokens(event):
                postings.setdefault(token, []).append(pos)
        self._postings = postings

    def __len__(self) -> int:
        return len(self._postings)

    def narrow(
        self, tokens: set[str], candidates: Sequence[tuple[str, Event]]
    ) -> Sequence[tuple[str, Event]] | None:
        """Candidates sharing a word with ``tokens``, or None to scan everything.

        None — not an empty list — is the "cannot speak" answer, so a stream the
        index knows nothing about keeps today's behaviour instead of silently
        matching nothing. An empty *list* is a real answer: the stream has
        tokens and no candidate shares one.

        ``candidates`` must be the sequence this index was built over; it is
        passed back in so a caller cannot accidentally narrow one list using
        another's positions.
        """
        if not tokens or candidates is not self._candidates:
            return None

        positions: set[int] = set()
        for token in tokens:
            hits = self._postings.get(token)
            if hits:
                positions.update(hits)

        # Preserve candidate order: ranking on equal scores reads date/time
        # proximity in sequence order, so a reordered shortlist could pick a
        # different winner among ties.
        return [candidates[pos] for pos in sorted(positions)]
