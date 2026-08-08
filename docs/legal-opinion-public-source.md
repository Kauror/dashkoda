# The public Koda.ee opinion source

DashKoda knows the Chamber's outgoing opinions from two places. The **private
source** is the Chamber-controlled folder of outgoing PDFs, catalogued by
`sync_legal_opinion_documents` and documented in
[legal-opinion-documents.md](legal-opinion-documents.md). The **public
source**, described here, is what Koda.ee itself publishes: opinion articles
under `Meie arvamus` and the news listing, and the full-opinion PDFs those
articles attach.

The private folder is **not exhaustive**. Reconciliation against the
legal-work workbook found a large share of sent matters with no corresponding
private PDF — and the absence of a known PDF is never evidence that no
opinion was submitted. The public source exists to recover what the Chamber
published but did not file, and to give readers the authoritative public
address when one exists.

## The concepts, kept apart

| Concept | Model | What it is |
| --- | --- | --- |
| Legal matter | `LegalMatter` | What the workbook says was handled and sent |
| Document bytes | `OpinionDocumentBlob` | One PDF, identified by its own SHA-256 |
| Reading | `OpinionDocumentExtraction` | One versioned extraction of one blob |
| Private provenance | `OpinionCatalogueEntry` | What one private source file claimed |
| Public provenance | `PublicOpinionDocument` | What one Koda.ee attachment claimed |
| Public page | `PublicOpinionPage` | One Koda.ee article holding opinion material |
| Opinion resource | `OpinionResource` | The one durable page per matter a topic links to |

Bytes are the document; where they were found is provenance. The same letter
filed privately and published publicly is **one blob with two provenances**,
never two documents — `OpinionDocumentBlob.sha256` is globally unique and
both sources resolve into it. When public and private PDFs differ in bytes
they stay distinct documents, because a revised or signed variant is not the
same file and pretending otherwise would invent an equivalence no evidence
established.

## Collection

`sync_public_opinions` walks two fixed listings on the one allowed host:

- `https://www.koda.ee/et/meie-arvamus` — an editorial view over news nodes.
  Being listed there is Koda.ee's own statement that an article is opinion
  material. Its teaser cards carry no year, so nothing date-bounded trusts
  them.
- `https://www.koda.ee/et/uudised` — the news listing, whose cards carry full
  dates and therefore drive date-bounded walking.

Detail pages live under `/et/uudised/`; attachments are direct `btn--file`
links under `/sites/default/files/`, and their filenames follow the Chamber's
own `date - recipient - subject` convention, so the private filename parser
reads them unchanged.

A page enters the corpus only with **opinion evidence**, recorded on the row:
listed under `Meie arvamus`, position wording in the article, or an
attachment whose filename parses as an opinion letter. Ordinary news naming a
statute is none of those.

Attachment bytes reuse the private pipeline end to end: the same validation
and quarantine rules, the same content-addressed store, the same versioned
extraction. The same SHA-256 under the current extractor version is never
extracted twice, whichever source supplied it first.

### Historical window

The activated window is **2025 onwards** (`KODA_OPINIONS_FROM_YEAR`). Older
public opinion history exists on Koda.ee and is deliberately not collected;
widening the window is a decision, not a crawl parameter someone happens to
change.

### Full and incremental

`--full` walks the whole window and must succeed once before incremental runs
are allowed. The daily incremental run reads only the listing edge
(`KODA_OPINIONS_INCREMENTAL_LISTING_PAGES` pages of each listing) plus a
re-read of articles published inside a short overlap window
(`KODA_OPINIONS_INCREMENTAL_OVERLAP_DAYS`), because Koda.ee attaches the
letter a day or two after publishing the article. Everything else is carried
forward untouched. Rerunning either mode is idempotent: a known attachment
URL whose blob exists is never downloaded again, and identical corpus content
publishes nothing.

### The corpus accumulates

Every published `PublicOpinionSnapshot` carries the full known corpus:
what this run read (`fetched`), what it restated from the previous snapshot
(`carried`), and what it could not read (`failed`). A page that leaves the
listing or answers 404 keeps its rows and its documents — `is_present` moves,
history does not. `first_seen_at` is copied forward from the snapshot that
first observed the page, so provenance keeps its original date.

### Failure behaviour

A failed run publishes nothing and the previous snapshot stays current,
exactly like every other feed. Failure is asymmetric on purpose:

- a **listing** that cannot be read fails the run — the edge is the one thing
  a run must see;
- a **known** detail page that cannot be read is carried forward and counted,
  and a 404 there moves `is_present`;
- a **new** detail page that cannot be read fails the run, because a snapshot
  claiming to cover the edge while missing an edge page would be a partial
  publication;
- a **new attachment** that cannot be fetched or validated is recorded as
  failed provenance and retried next run, because no blob pins it.

If Koda.ee is down for a morning, private opinions continue working, existing
public provenance survives, and accepted links do not move.

## Matching

One matcher (`opinion-1.2`) evaluates the whole opinion-document universe.
A candidate is one blob; private entries and public documents merge into it,
and where both describe the same blob the private description wins per-field
ties — a filename a person typed outranks one derived from an upload URL. A
public page's publication date joins the document's own dates as date
evidence. Weights and thresholds are unchanged from 1.1: the enlarged corpus
is measured against the same calibration before any retuning is considered.

`LegalOpinionMatchSnapshot` pins all of its inputs: the legal snapshot, the
private catalogue, and the public corpus (`public_opinion_snapshot`, null
when none has ever been published). `LegalOpinionDocumentRelation` carries
the blob and extraction directly plus whichever provenances exist, and must
carry at least one.

**Article-only pages** — a Koda.ee article confirming the position with no
attached PDF — can never become documents. A separate
`LegalOpinionPageRelation` attaches one to a decision only at the full
document-match bar *plus* date agreement, and a page claimed confidently by
two records goes to the stronger date agreement or to neither. Page evidence
never satisfies anything that requires a full PDF.

## What a reader sees

One `OpinionResource` per matter, at the same stable
`/oigusloome/arvamused/<opaque-id>/` address as before. The main document
action prefers, in order: the authoritative Koda.ee PDF, then the protected
DashKoda route. The protected route stays valid for every stored blob
whatever its provenance — it is the fallback a public URL's disappearance
cannot take away, and when both exist the page offers both without
duplicating the document. Confident article coverage renders as
“Vaata arvamust Koda.ee-s”, labelled an article, never an opinion PDF.

Both public addresses are re-validated at render time (HTTPS, allowlisted
host, fixed path prefix, no credentials); a stored URL that no longer passes
renders the protected route instead. Public PDFs are linked directly rather
than proxied — the authoritative public copy is Koda.ee's to serve — while
private documents continue through the protected route only.

Nothing on the page exposes a score, a matcher version, a digest, a storage
key or a filesystem path.

## Coverage, reviewed apart

`report_opinion_coverage --json` answers four different questions separately,
because one blended figure would mislead:

- **private source coverage** — sent matters a private document answers;
- **public exact recovery** — matters only the public corpus recovered;
- **combined document coverage** — matters any exact document answers;
- **automatic link coverage** — matters carrying an automatic link now;

with article-only confirmations counted apart from all four. Staff review
surfaces live in the read-only admin: provenance splits on relations,
dedup splits on public documents, failed fetches, and page relations with
their evidence.

## Operations

The prepared schedule slot is **06:25 Tallinn**, after the private catalogue
collection and before the matcher; `ops/unraid/generate_examples.py` is the
source of truth and `sync_public_opinions.sh.example` is generated from it.
The job is **not yet installed** on the pilot host. Deployment order, when it
happens: run the one-time `--full` walk manually, verify a second run reports
`unchanged`, then install the incremental slot.

Retention treats `PublicOpinionSnapshot` like every other source snapshot:
current and match-pinned snapshots are protected, retired history is pruned,
and deleting a snapshot never touches a PDF blob.

## What this source does not do

- It does not crawl search engines, follow off-host redirects, or fetch any
  URL a person can supply at runtime.
- It does not activate 2020–2024 history.
- It does not carry manual mappings: no hard-coded links, no SHA-to-record
  table, no title overrides. Manual review is a quality gate, not runtime
  infrastructure.
- It does not put a public URL into matter identity: URLs are provenance and
  navigation, and a changed attachment URL with identical bytes is the same
  blob.
- It does not make “no known document” mean “no opinion was sent”.
