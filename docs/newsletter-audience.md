# Newsletter audience

How many people the Chamber's three newsletters reach, read from Smaily once a
day by the scheduled `sync_smaily` command.

This replaced a number somebody read off Smaily's screen and typed into a form.
The manual path is gone: `apps/visibility/registry.py` marks the three
newsletter metrics `manual_entry=False`, so the entry form has no box for them
and the section says where the figures come from instead.

## What is collected

One request to `GET /api/list.php`, which returns one row per segment: an id, a
name and a subscriber count. That is the entire payload.

**Nothing else is requested and nothing else could be stored.** There is no
email address, name, phone number, subscriber ID, per-recipient open, click,
bounce or unsubscribe, IP address or device identifier anywhere in the schema —
`SmailyAudienceSnapshot` and `SmailySegmentDaily` have no column one could be
written into. Smaily returns recipient-level detail only when asked with
`detailed=1`, which this repository never sends; a response carrying
recipient-shaped keys is refused rather than parsed, and the error does not echo
the body.

## Read-only as a property of our code

Smaily's API users have no permission model. The credential that can read a list
can also delete it, create campaigns and send them. So "read-only" cannot be a
property of the credential and has to be a property of the integration:

- `apps/visibility/smaily.py` is the only module that issues a request;
- `SmailyApiClient._get` is the only request function, and its method is a
  literal `GET`;
- its endpoint argument is looked up in a fixed set, so no caller can steer a
  request at another path;
- the subdomain is validated against a DNS-label pattern before it is
  interpolated into a hostname, because the `Authorization` header goes wherever
  the URL points.

## Which segment is which newsletter

The account holds sixty segments and only five are durable lists. The rest are
one-off send audiences named after the day they were built — `09.06.26 emta`,
`24.04.26 margus` — so the mapping is an explicit registry in
`apps/visibility/smaily_segments.py` rather than a guess from a name.

| Newsletter | Segment | Audience |
| --- | --- | --- |
| e-Teataja | `2690` | Liikmed |
| e-Teataja | `2691` | Mitteliikmed |
| eNews | `2711` | — |
| e-Vestnik | `2692` | — |

**e-Teataja is two segments added together.** That is only defensible because
the two are disjoint by construction — one is the members' list, the other is
explicitly `mitteliikmed` — and the Chamber's own send practice confirms it:
each issue goes out as two campaigns, one per list. The two counts are stored
separately and shown separately beside the total, so if the assumption is ever
wrong the presentation changes and no history is lost.

The three *newsletters* are never added to each other. A reader subscribed to
both e-Teataja and eNews is one person and two subscriptions.

A segment is pinned by **id**, because a name is editable and an id is not, and
guarded by a token its name must still contain. A segment whose name has drifted
is **withheld**: that newsletter publishes no figure and a sentence says why,
the other two publish normally, and nothing is substituted or set to zero.

## Where the numbers go

Two places, in one transaction:

- `SmailySegmentDaily` — every segment the account holds, mapped or not, so a
  list the Chamber starts caring about next year already has history;
- `VisibilityObservation` — each newsletter's total, marked
  `CollectionMethod.AUTOMATIC` with no entry batch. Every existing reader (the
  overview's channel band, the Nähtavus page, freshness) asks this table, so the
  collector writes there through the same supersession path a typed correction
  uses.

A reading that finds nothing changed publishes neither. A reading that finds a
list has moved publishes a new revision naming the one it replaces; the replaced
revision keeps its figures.

## There is no backfill, and there cannot be one

Smaily reports what a list holds **now**. It has no endpoint that answers how
many subscribers a list had last March, so newsletter history starts on the day
collection started and grows forward.

This has an operational consequence worth stating plainly: **a day `sync_smaily`
does not run is a day of newsletter history nobody can recover.** Every other
collector in the schedule can be re-run over a range. This one cannot, which is
why `SmailyAudienceSnapshot` is in `NEVER_PRUNED` in
`apps/sources/retention.py`.

Interpolating between two known readings would fabricate exactly the series a
board would use to judge whether the newsletters are growing. Nothing does it.

## Configuration

Three environment variables, all empty by default. The application starts,
every page renders and the whole test suite runs without them.

```
SMAILY_SUBDOMAIN=
SMAILY_API_USERNAME=
SMAILY_API_PASSWORD=
```

`SMAILY_API_PASSWORD` is equivalent to full access to the account. It belongs in
the deployment environment only and must never reach Git, PostgreSQL, a log
line, an audit summary, command output or the interface. The command takes no
credential arguments, so none can enter shell history or a process listing.

Failure messages are written by this repository rather than passed through: a
`requests` exception carries the request URL, and the URL names the account's
subdomain. `SmailyFeedState.last_error_summary` is rendered in the admin and
holds only our own sentences.

## What an operator sees

    python manage.py sync_smaily --json

```json
{
  "result": "imported",
  "observed_on": "2026-08-10",
  "action": "imported",
  "segments_read": 60,
  "newsletters_available": 3,
  "newsletters_withheld": 0,
  "withheld": {},
  "api_requests": 1,
  "api_retries": 0
}
```

`withheld` carries metric keys and this repository's own sentences — never a
figure, a segment name or any part of Smaily's response.
