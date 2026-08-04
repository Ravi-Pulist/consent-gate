"""The consent record, and the decision it supports.

Consent in GDPR and in India's DPDP is permission to *use*, bound to a
purpose, revocable at will. It is not permission to *name*. That distinction
is the whole project: masking answers "who is this?" while consent governs
"may this be used at all?", and a system that masks is answering a question
nobody asked.

So the decision function here is deliberately boring — deny unless an
unrevoked consent exists for this subject, covering this purpose and this
scope, valid at this instant. Boring is the point. All of the engineering
risk lives in making sure this predicate is applied somewhere it cannot be
bypassed, not in the predicate itself.

**The subject is a keyed pseudonym and there is no linkage table anywhere in
this package.** A consent store is a register of who has and has not
permitted use of their records, which makes it a membership oracle by
construction. Giving it the ability to name people would make the system that
proves non-use into the most sensitive database in the deployment.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256

#: A query must name exactly one of these. A controlled vocabulary rather than
#: free text, because "purpose" that the caller can invent is not a
#: restriction — it is a text box that always says yes.
class Purpose(str, Enum):
    DIRECT_CARE = "direct_care"
    RESEARCH = "research"
    OUTREACH = "outreach"
    QUALITY_IMPROVEMENT = "quality_improvement"

    @classmethod
    def parse(cls, value: str | "Purpose") -> "Purpose":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError as exc:
            raise UnknownPurpose(
                f"{value!r} is not a declared purpose; permitted purposes are "
                f"{', '.join(p.value for p in cls)}") from exc


class ConsentError(Exception):
    """Base for every refusal. Callers that catch this must fail closed."""


class PurposeRequired(ConsentError):
    """A query arrived without a purpose. There is no default."""


class UnknownPurpose(ConsentError):
    """A purpose outside the controlled vocabulary."""


class StoreUnavailable(ConsentError):
    """The consent store could not answer. Never degrade to unfiltered."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pseudonym(subject_id: str, key: bytes) -> str:
    """Keyed HMAC pseudonym for a patient identifier.

    Keyed rather than a bare hash: an unkeyed digest of a national identifier
    or an MRN is reversible by anyone who can enumerate the space, which for
    most health identifiers is a laptop and an afternoon.
    """
    if not key:
        raise ValueError(
            "a pseudonym key is required; an unkeyed digest of a patient "
            "identifier is reversible by enumeration and is not a pseudonym")
    return hmac.new(key, subject_id.encode("utf-8"), sha256).hexdigest()[:32]


def key_from_env(var: str = "CONSENT_GATE_KEY") -> bytes:
    raw = os.environ.get(var)
    if not raw:
        raise ValueError(
            f"{var} is not set. Refusing to invent a pseudonym key: a key "
            "generated per process makes yesterday's consent records "
            "unmatchable, which fails open on the next query")
    return raw.encode("utf-8")


@dataclass(frozen=True)
class Consent:
    """One consent record. Append-only in the store; never updated in place.

    A revocation is a *new* record, not a mutation of the old one, so the
    consent state at any past instant stays reconstructable for the audit
    trail. A store that overwrites cannot answer "were we allowed to do that,
    then?" — which is the only question an investigator asks.
    """

    subject: str
    purposes: frozenset[Purpose]
    valid_from: datetime
    valid_until: datetime | None = None
    revoked_at: datetime | None = None
    #: which record types / collections this consent covers. Empty means all.
    scope: frozenset[str] = field(default_factory=frozenset)
    #: monotonically increasing per subject; stamped onto every cache entry
    version: int = 1

    def __post_init__(self) -> None:
        if not self.purposes:
            raise ValueError(
                f"consent for {self.subject} names no purpose; a consent that "
                "covers nothing is not a consent")
        for name, value in (("valid_from", self.valid_from),
                            ("valid_until", self.valid_until),
                            ("revoked_at", self.revoked_at)):
            if value is not None and value.tzinfo is None:
                raise ValueError(
                    f"{name} for {self.subject} is naive; a consent window "
                    "compared across timezones silently grants or withholds "
                    "permission by up to a day")
        if self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError(
                f"consent for {self.subject} expires at or before it begins")

    def covers(self, purpose: Purpose, scope: str | None = None) -> bool:
        if purpose not in self.purposes:
            return False
        return not self.scope or scope is None or scope in self.scope

    def active_at(self, when: datetime) -> bool:
        if self.revoked_at is not None and when >= self.revoked_at:
            return False
        if when < self.valid_from:            # future-dated: not yet active
            return False
        if self.valid_until is not None and when >= self.valid_until:
            return False                      # expired: no longer active
        return True


def allow(consents: list[Consent], purpose: Purpose, when: datetime,
          scope: str | None = None) -> bool:
    """Is use permitted for this purpose, at this instant, in this scope?

    Deny by default: an empty list is a denial, not an absence of opinion.
    Every caller in this package treats "I do not know" the same way — the
    quarantine discipline, applied to permission rather than to evidence.
    """
    return any(c.covers(purpose, scope) and c.active_at(when)
               for c in consents)


def consent_version(consents: list[Consent]) -> int:
    """The subject's current consent version, for stamping caches.

    Revocation bumps this, which is what makes a cached embedding or a cached
    result set unreadable rather than merely stale. A cache keyed only on the
    query is a way for a revoked record to keep answering questions.
    """
    return max((c.version for c in consents), default=0)
