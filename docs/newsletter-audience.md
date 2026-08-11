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

## Campaigns

`sync_smaily_campaigns` catalogues completed campaigns and publishes their
**aggregate** statistics. It is a separate command from `sync_smaily`, with its
own lock, so a campaign read that fails cannot make the subscriber figures look
stale.

Which newsletter an issue belongs to is decided from the **template name**.
Smaily has a `tags` field that would answer this exactly and it is empty on
every campaign in the account, so it cannot be used; the subject line is written
for readers and drifts. `mitteliikmed` is tested before `liikmed`, because it
contains it. A campaign that is not an issue of one of the three newsletters —
an event calendar, an Enterprise Europe Network mailing, a one-off invitation —
is catalogued and left unclassified rather than forced into a newsletter, where
it would land in that newsletter's open rate.

Classification is resolved once, when a campaign is first seen, and stored. A
template renamed afterwards does not move last year's issues.

Statistics are re-read only while they are still moving: every campaign
completed within a fortnight, plus any campaign that has none yet. A campaign
whose figures are unchanged publishes nothing; one whose figures have moved
publishes a new revision naming the one it replaces.

## Every completed send, not only the newsletters

**Collection first, classification second.** The collector asks Smaily for
`COMPLETED` campaigns and stores every one it gets; only afterwards does
`smaily_campaigns.classify` try to recognise which newsletter an issue belongs
to. A campaign it does not recognise is stored, shown, and labelled `Muu`.

That ordering is the whole point. The list used to `exclude(newsletter="")`
when rendering, which hid **2 105 of the account's 3 194 completed campaigns** —
event calendars, Enterprise Europe Network mailings, Christmas cards, export
bulletins, invitations, a Russian-language chamber bulletin — behind a
classifier that was only ever meant to label them. A recognition failure must
never look like a campaign that never happened.

Read from the live account on 2026-08-10:

| | |
| --- | --- |
| COMPLETED campaigns | **3 194** (2012-08-09 → 2026-08-04) |
| e-Teataja | 790 |
| eNews | 140 |
| e-Vestnik | 159 |
| `Muu` | 2 105 |
| DRAFT / PENDING / CANCELLED | 331 / 0 / 38 — never collected |

`Muu` rather than `Määramata`, because most of these are not unidentifiable —
they are simply *other*. A Kevadball invitation is not an unrecognised
e-Teataja.

e-Teataja stays primary through **presentation**: it leads the audience figures,
it is the newsletter whose aggregate rates are shown, and it is first in every
filter. It is not primary by hiding the rest.

## Opening a newsletter

Smaily supplies `template.preview_url` and DashKoda links to it. It never
constructs a preview address from a template ID, and it never fetches or stores
the newsletter HTML to build an archive of its own.

The address is **validated before it is stored**, because it is the one piece of
Smaily data that becomes an anchor a reader clicks: HTTPS only, on the account's
own `<subdomain>.sendsmaily.net` host, no embedded credentials, bounded length.
Anything else is dropped and the campaign is stored without a preview. Links
open in a new tab with `rel="noopener noreferrer"` and the usual hidden note
naming the destination.

### What the preview is, and is not

**It is not an immutable copy of what went out.** Smaily's field addresses the
*template*, not the campaign: on this account 147 templates are shared by more
than one campaign, one of them by eleven. So two sends can point at one preview,
and that preview renders whatever the template holds today — not what a reader
received in 2019.

DashKoda therefore stores the address Smaily associated with the campaign and
describes it as a preview, never as an archive. If a preview stops working, the
campaign and every figure it has remain.

### A deleted template

Smaily returns the literal string `"DELETED"` in place of the template object
when the template is gone — 67 campaigns on this account. That is not an import
failure and not a reason to drop the campaign:

- the campaign is stored and shown normally;
- its statistics are stored and shown;
- the preview link is simply absent, rather than present and broken.

A template deleted after a campaign was catalogued clears the stored address on
the next run, so no row keeps a link to a page that has gone.

## Rates and their denominators

No rate is stored. Smaily returns `opened_percent`, `click_percent` and
`view_percent`; all three are quotients of the counts beside them, and a rounded
copy would lose the denominator — which is the part that matters:

| Rate | Divided by | Why |
| --- | --- | --- |
| Avamismäär | delivered | A bounced message was never open-able, so dividing by *sent* would understate every campaign by its bounce rate. This is what Smaily's own percentage means. |
| Klikimäär | delivered | Unique clickers, not clicks: one reader following six links is one interested reader, not six. |
| Klikke avajate seas | opens | A different question — what share of those who looked went on to follow something. |

An **aggregate** rate across several issues is summed counts over summed counts,
never the mean of per-issue percentages. Averaging the percentages would weight
a send to 755 people the same as one to 20 616, and the headline figure would
drift towards whichever list is smallest.

## On the page

The Nähtavus page gains a `Uudiskirjade tulemused` section: each list's size
over time, and under `Viimased saadetud uudiskirjad` the most recent completed
sends of every kind. The filter is `Kõik | e-Teataja | eNews | e-Vestnik | Muu`,
defaulting to `Kõik`; `Muu` appears only when unclassified sends exist.
Filtering to one newsletter adds its aggregate figures above the table.

`/nahtavus/uudiskirjad/` is the archive behind it — every completed send, 50 to
a page, filterable by type and searchable by subject. The search runs against
the stored subject in PostgreSQL and never contacts Smaily. Fourteen years of
campaigns is not a section, which is why it is its own page.

The audience chart starts where collection started and says so, because there is
no earlier history to draw and padding it would show three newsletters being
founded on the morning the collector was deployed.

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
