"""One implementation of the published-record write guard.

A published domain record is immutable: a correction creates a new record that
supersedes the old one, and there is no delete action. Every domain enforced
that with its own hand-written `save()`, which is how one pair of models came to
declare `MUTABLE_FIELDS` and then never check it — the guard was simply
forgotten, and nothing could notice.

The rule is the same everywhere, so it is written once here. What stays with the
model is what genuinely differs: which fields may still move, what to raise, and
what to tell whoever tried.

## Declaring a guarded model

```python
class NewsItem(ImmutableWriteGuard, models.Model):
    IMMUTABLE_ERROR = NewsImmutable
    IMMUTABLE_MESSAGE = "An imported news item cannot be changed."
```

Frozen by default: with no `MUTABLE_FIELDS`, any write to an existing row is
refused. A record with fields that may still be re-observed names them, and only
a save restricted to those fields is allowed:

```python
class NewsSnapshot(ImmutableWriteGuard, models.Model):
    MUTABLE_FIELDS = frozenset({"is_current"})
    IMMUTABLE_ERROR = NewsImmutable
    IMMUTABLE_MESSAGE = "An imported news snapshot may only change its is_current flag."
```

`IMMUTABLE_MESSAGE` may contain `{fields}`, which renders as the sorted mutable
field names — for a message that cannot fall out of step with the set it
describes.

## The one deliberate variation

`ALLOW_UNRESTRICTED_SAVE` decides what an unrestricted `save()` — one naming no
`update_fields` at all — means. The default is `False`: such a save rewrites
every column, so a partly mutable record refuses it.

Two models set it `True`, which is how they were already written before this
guard existed. It is preserved rather than quietly tightened, because tightening
it is a behavioural decision about two catalogues and does not belong in a
change that only moves code. See the note in `docs/data-model.md`.

This guard covers writes only. Deleting is refused by a handful of models with
their own `delete()`, which stays where it is until one of those domains adopts
this — an unused branch here would be a guess about what they need.
"""

from __future__ import annotations


class ImmutableWriteGuard:
    """Refuses writes to an already-published row.

    Deliberately a plain mixin rather than an abstract Django model: it carries
    no field, adds no `Meta`, and therefore cannot appear in a migration. Place
    it before `models.Model` in the bases so its `save()` runs first.
    """

    #: Fields that may still be written once the row exists. Empty means frozen.
    MUTABLE_FIELDS: frozenset[str] = frozenset()

    #: Raised when a write would change what is already published. Every domain
    #: keeps its own type, so `except NewsImmutable` still means news.
    IMMUTABLE_ERROR: type[Exception] = RuntimeError

    #: What the refusal says. May contain `{fields}`.
    IMMUTABLE_MESSAGE: str = "A published record cannot be changed."

    #: Whether a `save()` naming no `update_fields` is permitted. See above.
    ALLOW_UNRESTRICTED_SAVE: bool = False

    def _immutable_message(self) -> str:
        if "{fields}" in self.IMMUTABLE_MESSAGE:
            return self.IMMUTABLE_MESSAGE.format(fields=sorted(self.MUTABLE_FIELDS))
        return self.IMMUTABLE_MESSAGE

    def _refuse_immutable_write(self, update_fields) -> None:
        """Raise unless this write is confined to the mutable fields."""
        if not self.MUTABLE_FIELDS:
            # Frozen: no write to an existing row is permitted, including the
            # empty `update_fields=[]` that Django would treat as a no-op. The
            # models this replaced refused that too, and a guard that answers
            # "nothing may change" with "that particular nothing is fine" is a
            # harder rule to state than the one it saves.
            raise self.IMMUTABLE_ERROR(self._immutable_message())
        if update_fields is None:
            # A save naming no fields writes every column. Permitted only where
            # the model says so.
            if self.ALLOW_UNRESTRICTED_SAVE:
                return
            raise self.IMMUTABLE_ERROR(self._immutable_message())
        if not set(update_fields) <= self.MUTABLE_FIELDS:
            raise self.IMMUTABLE_ERROR(self._immutable_message())

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            self._refuse_immutable_write(kwargs.get("update_fields"))
        return super().save(*args, **kwargs)
