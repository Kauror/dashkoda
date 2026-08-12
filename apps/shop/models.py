"""What DashKoda knows about the Koda.ee web shop.

Five things are modelled, and they are five rather than one because they have
different lifetimes:

- **`ShopProduct`** — an identity. One row per Drupal Commerce product, keyed by
  the Commerce product ID and nothing else. A title is display metadata; a
  product that is renamed is the same product, and joining on titles would make
  `Tähtajaline tööleping` and `Tähtajaline tööleping renditöötajaga` compete for
  one row.
- **`ShopProductSnapshot`** — catalogue metadata as it was *observed on a day*.
  Drupal keeps no price history, so today's price is a reading rather than a
  fact about the past. Modelling it as a timeless attribute would let a 2021
  purchase be valued at a 2026 price.
- **`ShopProductPage`** — which public addresses a product has. A contract
  template has two: an informational page and the product page itself. They are
  separate rows because they are separate pages with separate traffic.
- **`ShopDailyFact`** — the transactional grain, aggregated. One row per day per
  product per member status per payment class. This is the only model carrying
  money and counts, and it holds no customer of any kind.
- **`ShopDailySummary`** — how many *distinct* orders a day carried, which the
  fact grain cannot answer: an order of three templates lands in three cells.
  Counted at import, where the identifiers still exist, and only the total
  survives.
- **`ShopSourceState`** — what the source covers and which of its semantics have
  been verified. Without it a stale export looks exactly like a quiet month.

## Revisions, not rewrites

Every published row follows the repository rule that a published domain fact is
immutable: a correction inserts a **new current row** naming the one it
replaces, and the replaced row keeps its figures. `Ga4DailySnapshot` is the
pattern being followed, down to the partial unique index that guarantees exactly
one current row per natural key — which is what stops a chart counting one
Tuesday twice.

Revisioning per row rather than per import is what lets today's manual full
export and a later incremental collector publish into the same tables without
either the models or the selectors changing.

## Nothing here can hold a person

There is deliberately no field capable of storing a customer name, an email
address, a telephone number, a postal address, a participant, a registry code, a
VAT number, a transaction ID, a cart token or a webform body. `member_status` is
a three-valued aggregate dimension and `payment_class` collapses eight payment
methods into two plus unknown; neither identifies anybody. The importer refuses
unknown columns outright, so a source that grows an `email` column fails the
import rather than quietly filling an object with it.

## Membership is not in scope

Drupal Commerce processes membership purchases as orders, but membership belongs
to the membership domain. `ProductType` has no `membership` member and the
importer rejects the value, so a source offering membership rows cannot quietly
add them to shop figures.
"""

from __future__ import annotations

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.sources.models import DataSource, ImportRun

#: Money is `Decimal` everywhere, never float. Four decimal places because
#: Drupal Commerce stores prices at six and real Chamber values never use more
#: than four; two would silently round a source value on import.
MONEY_DIGITS = 14
MONEY_PLACES = 4

#: Quantity is decimal because a Commerce order item's quantity is `1.00`, and
#: assuming it can only ever be whole is the kind of assumption that survives
#: right up until a source uses a half.
QUANTITY_DIGITS = 12
QUANTITY_PLACES = 2

#: The only currency the Chamber's shop uses. Stored explicitly anyway: a
#: currency inferred from context is a currency nobody can check.
DEFAULT_CURRENCY = "EUR"


class ShopImmutable(RuntimeError):
    """Raised when something tries to rewrite a published shop fact."""


class ProductType(models.TextChoices):
    """The storefront product families this module covers.

    `membership` and `default` exist in Koda.ee Commerce and are deliberately
    absent: membership is the membership domain's fact even though Commerce
    processes the order, and `default` is a single technical row.
    """

    DOCUMENT = "document", "Lepingute näidised"
    EVENT_REGISTRATION = "event_registration", "Sündmused"
    PHYSICAL_PRODUCT = "physical_product", "Füüsilised tooted"


class MemberStatus(models.TextChoices):
    """Who acquired, as an aggregate dimension.

    `UNKNOWN` is a real and expected answer, not a placeholder for zero. A source
    that cannot state membership for a historical order must say so rather than
    guess, and the interface keeps the whole dimension hidden until
    `ShopSourceState.member_semantics_verified` confirms the source establishes
    membership **at transaction time** rather than as it stands today.
    """

    MEMBER = "member", "Liige"
    NON_MEMBER = "non_member", "Mitteliige"
    UNKNOWN = "unknown", "Teadmata"


class PaymentClass(models.TextChoices):
    """How it was paid, collapsed to what analytics can honestly use.

    Koda.ee distinguishes eight methods. Seven of them are a bank link or a card
    and settle immediately; the eighth is an invoice, which the website records
    as *sent* and never as *paid*. That distinction is the only one that changes
    what a figure means, so it is the only one stored.
    """

    INVOICE = "invoice", "Arve"
    BANK_OR_CARD = "bank_or_card", "Pangalink või kaart"
    UNKNOWN = "unknown", "Teadmata"


class PageRole(models.TextChoices):
    """What a public page *is* to a product.

    The distinction exists because a contract template has two pages and they
    answer different questions. `INFORMATION` is the explanatory page a reader
    arrives on; `PRODUCT` is the page carrying the buy action and therefore the
    only honest denominator for an acquisition rate; `EVENT` is the public event
    page an event-registration product is sold through.
    """

    PRODUCT = "product", "Tooteleht"
    INFORMATION = "information", "Tutvustus"
    EVENT = "event", "Sündmuse leht"


class ShopProduct(models.Model):
    """One Koda.ee Commerce product, identified by its Commerce product ID.

    Carries no title, no price and no category: all three are observations that
    belong to a dated snapshot. What lives here is only what cannot change
    without the product becoming a different product.
    """

    source_product_id = models.PositiveBigIntegerField(
        unique=True, db_index=True, verbose_name="Commerce toote ID"
    )
    product_type = models.CharField(
        max_length=32, choices=ProductType, db_index=True, verbose_name="Tooteliik"
    )
    first_seen_on = models.DateField(verbose_name="Esmakordselt nähtud")
    last_seen_on = models.DateField(verbose_name="Viimati nähtud")
    created_at = models.DateTimeField(auto_now_add=True)

    #: Identity is fixed. Only the observation window may move.
    MUTABLE_FIELDS = frozenset({"last_seen_on"})

    class Meta:
        ordering = ("source_product_id",)
        verbose_name = "E-poe toode"
        verbose_name_plural = "E-poe tooted"

    def __str__(self) -> str:
        return f"#{self.source_product_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and not set(update_fields) <= self.MUTABLE_FIELDS:
                raise ShopImmutable(
                    "A shop product keeps its Commerce ID and type; only "
                    f"{sorted(self.MUTABLE_FIELDS)} may be re-observed."
                )
        return super().save(*args, **kwargs)


class ShopProductSnapshot(models.Model):
    """Catalogue metadata for one product as observed on one day.

    **Everything here is current-state data with a date attached, never history.**
    Drupal retained no price revisions, so the earliest list price DashKoda can
    ever know is the one in the first import. That is precisely why the price
    pair is stored per observation date: it makes a future member-benefit
    calculation possible *from this date onward*, and it makes retrospectively
    valuing a 2021 purchase at a 2026 price structurally impossible rather than
    merely discouraged.

    `published` and `publicly_listed` are two different questions and are two
    different fields. The 2026-08-11 audit found 273 published document products
    and only 144 of them listed in the public shop, and nobody has yet
    established why. Both may be null, and the interface refuses to present a
    "currently in the shop" population until
    `ShopSourceState.public_listing_semantics_verified` is true.
    """

    product = models.ForeignKey(
        ShopProduct, on_delete=models.PROTECT, related_name="snapshots", verbose_name="Toode"
    )
    observed_on = models.DateField(db_index=True, verbose_name="Vaatluse kuupäev")

    title = models.TextField(verbose_name="Pealkiri")
    category_term_id = models.PositiveIntegerField(
        null=True, blank=True, db_index=True, verbose_name="Kategooria ID"
    )
    category_name = models.CharField(max_length=200, blank=True, verbose_name="Kategooria")

    published = models.BooleanField(null=True, blank=True, verbose_name="Avaldatud")
    publicly_listed = models.BooleanField(null=True, blank=True, verbose_name="E-poes nähtav")
    members_only = models.BooleanField(null=True, blank=True, verbose_name="Ainult liikmele")

    list_price_net = models.DecimalField(
        max_digits=MONEY_DIGITS,
        decimal_places=MONEY_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="Hind (KM-ta)",
    )
    member_price_net = models.DecimalField(
        max_digits=MONEY_DIGITS,
        decimal_places=MONEY_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="Liikme hind (KM-ta)",
    )
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY, verbose_name="Valuuta")

    connected_event_node_id = models.PositiveBigIntegerField(
        null=True, blank=True, db_index=True, verbose_name="Seotud sündmuse node ID"
    )

    is_current = models.BooleanField(default=True, db_index=True, verbose_name="Kehtiv")
    supersedes = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="superseded_by",
        verbose_name="Asendab",
    )
    import_run = models.ForeignKey(
        ImportRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="shop_product_snapshots",
        verbose_name="Impordijooks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    #: Only the current flag moves after publication; a correction is a new row.
    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-observed_on", "product_id")
        verbose_name = "E-poe toote vaatlus"
        verbose_name_plural = "E-poe toodete vaatlused"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "observed_on"],
                condition=Q(is_current=True),
                name="shopproductsnapshot_one_current_per_day",
            ),
            models.CheckConstraint(
                condition=Q(list_price_net__isnull=True) | Q(list_price_net__gte=0),
                name="shopproductsnapshot_list_price_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(member_price_net__isnull=True) | Q(member_price_net__gte=0),
                name="shopproductsnapshot_member_price_not_negative",
            ),
        ]
        indexes = [models.Index(fields=["product", "-observed_on"])]

    def __str__(self) -> str:
        return f"{self.title[:60]} ({self.observed_on:%d.%m.%Y})"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise ShopImmutable(
                    "A published product observation is immutable; a correction "
                    "creates a new current row that supersedes it."
                )
        return super().save(*args, **kwargs)


class ShopProductPage(models.Model):
    """One canonical public path a product is reachable at, in one role.

    Rows are **kept** when a path changes: the old path is marked not current
    rather than deleted, because GA4 still holds traffic filed under it and a
    page that existed is provenance.

    `path` is stored already canonical — the value
    `apps.visibility.ga4_paths.canonical_path` produces — so a GA4 join is an
    indexed equality match rather than a URL parsed on every row of every
    ranking. It is the importer's job to canonicalise; nothing downstream
    re-derives it, because two normalisations is how one product's traffic ends
    up split across two spellings of its own address.
    """

    product = models.ForeignKey(
        ShopProduct, on_delete=models.PROTECT, related_name="pages", verbose_name="Toode"
    )
    page_role = models.CharField(
        max_length=16, choices=PageRole, db_index=True, verbose_name="Lehe roll"
    )
    path = models.CharField(max_length=500, db_index=True, verbose_name="Tee")

    first_seen_on = models.DateField(verbose_name="Esmakordselt nähtud")
    last_seen_on = models.DateField(verbose_name="Viimati nähtud")
    is_current = models.BooleanField(default=True, db_index=True, verbose_name="Kehtiv")
    import_run = models.ForeignKey(
        ImportRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="shop_product_pages",
        verbose_name="Impordijooks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE_FIELDS = frozenset({"is_current", "last_seen_on"})

    class Meta:
        ordering = ("product_id", "page_role", "path")
        verbose_name = "E-poe toote leht"
        verbose_name_plural = "E-poe toodete lehed"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "page_role", "path"],
                name="shopproductpage_unique_product_role_path",
            ),
            models.UniqueConstraint(
                fields=["product", "page_role"],
                condition=Q(is_current=True),
                name="shopproductpage_one_current_per_role",
            ),
            models.CheckConstraint(
                condition=Q(path__startswith="/"),
                name="shopproductpage_path_is_absolute",
            ),
        ]
        indexes = [models.Index(fields=["path", "page_role"])]

    def __str__(self) -> str:
        return f"{self.get_page_role_display()}: {self.path}"


class ShopDailyFact(models.Model):
    """Completed Commerce activity for one product on one day, one dimension cell.

    **Completed means the Drupal Commerce order `state`, and nothing else.** The
    Koda.ee order entity also carries a custom `field_order_completed` flag; the
    2026-08-11 audit found 12 618 orders flagged "Lõpetamata" whose Commerce
    state was `Completed`, so using that flag as a sales status understates sales
    roughly fourfold. The importer accepts `completed` and refuses every other
    value, and `tests/shop/test_import_contract.py` holds that rule.

    `ordered_value_net` is **ordered value excluding VAT**, not revenue. Koda.ee
    records no payment receipt and no refund: 77% of orders are invoices the
    website marks as sent and never as paid. Recognised revenue is the
    accounting system's fact and this application does not have it.

    The grain is deliberately one row per cell rather than one row per order, so
    the table holds counts and money and can hold nothing about a customer.
    """

    report_date = models.DateField(db_index=True, verbose_name="Kuupäev")
    product = models.ForeignKey(
        ShopProduct, on_delete=models.PROTECT, related_name="daily_facts", verbose_name="Toode"
    )
    member_status = models.CharField(
        max_length=16,
        choices=MemberStatus,
        default=MemberStatus.UNKNOWN,
        db_index=True,
        verbose_name="Liikmestaatus",
    )
    payment_class = models.CharField(
        max_length=16,
        choices=PaymentClass,
        default=PaymentClass.UNKNOWN,
        db_index=True,
        verbose_name="Makseviis",
    )
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY, verbose_name="Valuuta")

    #: Orders in **this cell** — that is, orders containing this product on this
    #: day under this member status and payment class. It is therefore additive
    #: across days for one product, and **not** additive across products: an
    #: order carrying three templates contributes one to each of three cells.
    #: Summing it over a whole catalogue counts order *lines*, which is why the
    #: overview says `Tellimusridu` and only a product's own page says
    #: `Tellimused`. On the first real dataset the difference is 5 551 lines
    #: against 4 052 distinct orders.
    order_count = models.PositiveIntegerField(verbose_name="Tellimusridu")
    units = models.DecimalField(
        max_digits=QUANTITY_DIGITS,
        decimal_places=QUANTITY_PLACES,
        validators=[MinValueValidator(0)],
        verbose_name="Soetatud ühikuid",
    )
    ordered_value_net = models.DecimalField(
        max_digits=MONEY_DIGITS,
        decimal_places=MONEY_PLACES,
        validators=[MinValueValidator(0)],
        verbose_name="Tellitud väärtus (KM-ta)",
    )

    #: How the units split between free and paid, classified line by line at
    #: import rather than inferred from this cell's total afterwards — a cell
    #: mixing a free acquisition with a paid one has a positive total and would
    #: read as entirely paid.
    #:
    #: All three are null together when the source did not classify (schema 1.0
    #: packages), and that is a third state: **not stated**, distinct from an
    #: explicit zero. When present they sum to `units`, which a constraint below
    #: enforces.
    free_units = models.DecimalField(
        max_digits=QUANTITY_DIGITS,
        decimal_places=QUANTITY_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="Tasuta ühikuid",
    )
    paid_units = models.DecimalField(
        max_digits=QUANTITY_DIGITS,
        decimal_places=QUANTITY_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="Tasulisi ühikuid",
    )
    unknown_units = models.DecimalField(
        max_digits=QUANTITY_DIGITS,
        decimal_places=QUANTITY_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="Määramata ühikuid",
    )

    is_current = models.BooleanField(default=True, db_index=True, verbose_name="Kehtiv")
    supersedes = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="superseded_by",
        verbose_name="Asendab",
    )
    import_run = models.ForeignKey(
        ImportRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="shop_daily_facts",
        verbose_name="Impordijooks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-report_date", "product_id")
        verbose_name = "E-poe päevafakt"
        verbose_name_plural = "E-poe päevafaktid"
        constraints = [
            models.UniqueConstraint(
                fields=["report_date", "product", "member_status", "payment_class", "currency"],
                condition=Q(is_current=True),
                name="shopdailyfact_one_current_per_cell",
            ),
            models.CheckConstraint(
                condition=Q(units__gte=0), name="shopdailyfact_units_not_negative"
            ),
            models.CheckConstraint(
                condition=Q(ordered_value_net__gte=0),
                name="shopdailyfact_value_not_negative",
            ),
            # Either the source classified every unit or it classified none.
            # A partial split would let a template add three numbers that do not
            # make the whole and show a free share of the wrong denominator.
            models.CheckConstraint(
                condition=(
                    Q(free_units__isnull=True, paid_units__isnull=True, unknown_units__isnull=True)
                    | Q(
                        free_units__isnull=False,
                        paid_units__isnull=False,
                        unknown_units__isnull=False,
                    )
                ),
                name="shopdailyfact_split_all_or_nothing",
            ),
        ]
        indexes = [
            models.Index(fields=["product", "report_date"]),
            models.Index(fields=["report_date", "member_status"]),
        ]

    def __str__(self) -> str:
        return f"{self.report_date:%d.%m.%Y} #{self.product_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise ShopImmutable(
                    "A published shop fact is immutable; a correction creates a "
                    "new current row that supersedes it."
                )
        return super().save(*args, **kwargs)


class ShopDailySummary(models.Model):
    """How many **distinct** Commerce orders were placed on one day.

    `ShopDailyFact` cannot answer this. Its grain is one row per product per
    dimension cell, so an order carrying three templates contributes to three
    cells and summing it counts that order three times. On the real dataset
    11.5% of orders carry more than one line — one carries 33 — and the gap
    between 5 627 lines and 4 056 orders is 39%.

    So the count is computed where the order identifiers still exist, at import,
    and only the total survives. **No order number, order ID or customer field
    is stored anywhere in this application**; the importer reads identifiers to
    count them and discards them in the same pass.

    ## Why product type, and why a blank row beside it

    A row per `product_type` lets the page answer "how many contract orders",
    and a row with `product_type = ""` carries the true count for the day across
    every type. Both are needed because an order containing a document and a
    physical product belongs to two type rows but is one order: adding the type
    rows would count it twice, exactly the error this model exists to remove.

    Deliberately **no category dimension**. One order routinely spans
    categories, so a per-category distinct count cannot be summed into anything,
    and offering one would invite precisely that.
    """

    #: The row that counts every type together. Not a product type, and stored
    #: as a blank rather than null so the unique constraint can include it.
    ALL_TYPES = ""

    report_date = models.DateField(db_index=True, verbose_name="Kuupäev")
    product_type = models.CharField(
        max_length=32,
        blank=True,
        db_index=True,
        verbose_name="Tooteliik",
        help_text="Tühi tähendab kõiki tooteliike kokku.",
    )
    distinct_order_count = models.PositiveIntegerField(verbose_name="Eri tellimusi")

    is_current = models.BooleanField(default=True, db_index=True, verbose_name="Kehtiv")
    supersedes = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="superseded_by",
        verbose_name="Asendab",
    )
    import_run = models.ForeignKey(
        ImportRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="shop_daily_summaries",
        verbose_name="Impordijooks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-report_date", "product_type")
        verbose_name = "E-poe päevakokkuvõte"
        verbose_name_plural = "E-poe päevakokkuvõtted"
        constraints = [
            models.UniqueConstraint(
                fields=["report_date", "product_type"],
                condition=Q(is_current=True),
                name="shopdailysummary_one_current_per_day_and_type",
            ),
        ]
        indexes = [models.Index(fields=["report_date", "product_type"])]

    def __str__(self) -> str:
        return f"{self.report_date:%d.%m.%Y} {self.product_type or 'kõik'}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise ShopImmutable(
                    "A published daily summary is immutable; a correction creates "
                    "a new current row that supersedes it."
                )
        return super().save(*args, **kwargs)


class ShopSourceState(models.Model):
    """What the current shop dataset covers, and which semantics are trusted.

    Without this row the interface cannot tell a stale export from a quiet
    month, and that is the single most dangerous confusion available here: GA4
    keeps collecting after the Commerce export stops, so September traffic
    divided by "no September orders" would produce a conversion rate of zero for
    a month nobody has imported yet. `coverage_end` is what every combined
    metric clamps to.

    The two verification flags are gates, not decoration. Each defaults to
    false, and the interface withholds the whole dimension rather than showing an
    unverified one:

    - **`member_semantics_verified`** — whether the source establishes membership
      as it was **at the moment of the transaction**. Drupal Commerce normally
      snapshots the billing profile onto the order, which would make it so, but
      that was not confirmed against this site. If profiles are instead reused
      and updated, the flag reflects membership *today* and every historical
      split would be wrong.
    - **`public_listing_semantics_verified`** — whether "listed in the public
      shop" is established, as opposed to merely "published in Drupal".
    """

    source = models.ForeignKey(
        DataSource,
        on_delete=models.PROTECT,
        related_name="shop_states",
        verbose_name="Andmeallikas",
    )
    import_run = models.ForeignKey(
        ImportRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="shop_states",
        verbose_name="Impordijooks",
    )

    schema_version = models.CharField(max_length=16, verbose_name="Skeemi versioon")
    source_as_of = models.DateField(verbose_name="Andmed seisuga")
    coverage_start = models.DateField(verbose_name="Ajaloo algus")
    coverage_end = models.DateField(verbose_name="Ajaloo lõpp")

    member_semantics_verified = models.BooleanField(
        default=False, verbose_name="Liikmestaatuse tähendus kinnitatud"
    )
    public_listing_semantics_verified = models.BooleanField(
        default=False, verbose_name="E-poes nähtavuse tähendus kinnitatud"
    )

    content_checksum = models.CharField(max_length=64, verbose_name="Sisu räsi")
    product_count = models.PositiveIntegerField(default=0, verbose_name="Tooteid")
    fact_count = models.PositiveIntegerField(default=0, verbose_name="Päevafakte")
    page_count = models.PositiveIntegerField(default=0, verbose_name="Lehti")

    observed_at = models.DateTimeField(verbose_name="Imporditud")
    is_current = models.BooleanField(default=True, db_index=True, verbose_name="Kehtiv")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE_FIELDS = frozenset({"is_current"})

    class Meta:
        ordering = ("-observed_at", "-id")
        verbose_name = "E-poe andmeallika olek"
        verbose_name_plural = "E-poe andmeallika olekud"
        constraints = [
            models.UniqueConstraint(
                fields=["source"],
                condition=Q(is_current=True),
                name="shopsourcestate_one_current_per_source",
            ),
            models.CheckConstraint(
                condition=Q(coverage_end__gte=models.F("coverage_start")),
                name="shopsourcestate_coverage_end_not_before_start",
            ),
            models.CheckConstraint(
                condition=Q(coverage_end__lte=models.F("source_as_of")),
                name="shopsourcestate_coverage_within_source_date",
            ),
        ]

    def __str__(self) -> str:
        return f"E-poe andmed seisuga {self.source_as_of:%d.%m.%Y}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise ShopImmutable(
                    "A published source state is immutable; a new import creates a new current row."
                )
        return super().save(*args, **kwargs)


__all__ = [
    "DEFAULT_CURRENCY",
    "MemberStatus",
    "PageRole",
    "PaymentClass",
    "ProductType",
    "ShopDailyFact",
    "ShopDailySummary",
    "ShopImmutable",
    "ShopProduct",
    "ShopProductPage",
    "ShopProductSnapshot",
    "ShopSourceState",
]
