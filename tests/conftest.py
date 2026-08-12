import pytest
from django.contrib.auth.hashers import make_password

# The synthetic viewer PIN every test authenticates with. One constant, so the
# hash below and the `viewer_pin` fixture cannot drift apart.
VIEWER_PIN = "8642"


@pytest.fixture(scope="session")
def viewer_pin_hash():
    """The PIN's Django hash, computed once for the whole session.

    `make_password` is deliberately expensive — production-strength PBKDF2,
    about a second of key stretching per call. Hashing inside the autouse
    fixture below meant every test paid that second before it started, which
    made the hash, not the tests, the suite's dominant cost: the DB-free half
    of the suite measured 1,495 tests in 26 minutes, ~0.99 s per test, against
    ~1 s per hash. One hash is as synthetic as three thousand.

    The hash itself stays real: `check_password` in the login tests still
    exercises the production hasher against a production-shaped value.
    """
    return make_password(VIEWER_PIN)


@pytest.fixture(autouse=True)
def viewer_access_settings(settings, viewer_pin_hash):
    settings.VIEWER_PIN_HASH = viewer_pin_hash
    settings.VIEWER_PIN_VERSION = 3
    settings.VIEWER_RATE_LIMIT_SECRET = "synthetic-test-rate-limit-secret"
    settings.TRUST_CLOUDFLARE_IP_HEADER = False


@pytest.fixture(autouse=True)
def private_artifact_root(settings, tmp_path):
    """Point private artifact storage at a per-test temporary directory.

    pytest removes it afterwards, so no test ever writes into the repository or
    leaves an original file behind.
    """
    root = tmp_path / "source-artifacts"
    root.mkdir()
    settings.SOURCE_ARTIFACT_ROOT = str(root)
    return root


@pytest.fixture
def populated_migration(transactional_db):
    """Run a migration against a database that already holds rows.

    ```python
    def test_the_backfill_survives_existing_rows(populated_migration):
        def seed(apps):
            apps.get_model("legal_work", "OpinionDocumentBlob").objects.create(...)

        apps = populated_migration(
            "legal_work", before="0005_...", after="0006_...", seed=seed
        )
        assert ...  # read the rows back out of `apps`
    ```

    Depends on `transactional_db` on purpose. Migrations need real commits, so
    the usual wrapped-transaction database would not do — and requesting it here
    means a test cannot forget `django_db(transaction=True)` and get a confusing
    failure instead of a clear one.

    Teardown restores every app it touched to its leaf migration, **including
    when the test fails**. The test database is shared for the whole session, so
    an app left at an older migration would break every test after it. That
    ordering is why the fixture depends on `transactional_db` rather than the
    other way round: the schema is put back before the table flush runs.

    See `tests/migration_harness.py` for the mechanics and `AGENTS.md` for which
    migrations are required to have one of these.
    """
    from .migration_harness import leaf_migration, migrate_to, run_populated_migration

    touched: set[str] = set()

    def run(app_label: str, *, before: str, after: str, seed):
        touched.add(app_label)
        return run_populated_migration(app_label, before=before, after=after, seed=seed)

    yield run

    for app_label in sorted(touched):
        migrate_to(leaf_migration(app_label))


@pytest.fixture
def viewer_pin():
    return VIEWER_PIN


@pytest.fixture
def authenticate_viewer():
    def authenticate(client, *, version=3):
        session = client.session
        session["viewer_authenticated"] = True
        session["viewer_pin_version"] = version
        session.save()

    return authenticate
