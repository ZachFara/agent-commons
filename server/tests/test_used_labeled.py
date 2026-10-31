"""`used` v1 against v0 on labeled sessions (decision 0036).

True uses come from the eval's known-entry scenarios and the production entries; false ones
include the 2026-10-31 deploy check that v0 counted as a partial use of the Keycloak entry.
The shipped detector must be at least as precise as v0 and lose no true use.
"""

from dataclasses import dataclass

from commons.domain.usage import Evidence, count_artifacts, fix_artifacts, observe_use

KEYCLOAK = (
    "For a local-only stack, set the realms to not require SSL via kcadm inside the Keycloak "
    "container:\ndocker exec full-cosmo-keycloak-1 sh -c '/opt/keycloak/bin/kcadm.sh config "
    "credentials --server http://localhost:8080 --realm master --user admin --password changeme "
    "&& /opt/keycloak/bin/kcadm.sh update realms/cosmo -s sslRequired=NONE'\nThen reload "
    "Studio. Re-run after `docker compose ... down -v` because the seed restores "
    "sslRequired=external."
)
METRICS = (
    "Point the router at the local collector too: add "
    "-e GRAPHQL_METRICS_COLLECTOR_ENDPOINT=http://host.docker.internal:4005 (or "
    "graphql_metrics.collector_endpoint in config.yaml). After ~60-75s, `wgc subgraph check` "
    "reports operations impacted."
)
RENAME = "Rename `ttl=` to `expire_after=` where you construct `CacheConfig`."
STRICT = "Pass `strict=False` to `read_rows` to pad short rows as 2.x did."
RETRIES = 'Pass `Client(transport={"retries": 3})` instead of `Client(retries=3)`.'
PIN = "Pin `markupsafe<2.1`, run `pip check`, and set NODE_OPTIONS=--openssl-legacy-provider."
STORE = [KEYCLOAK, METRICS, RENAME, STRICT, RETRIES, PIN]


@dataclass(frozen=True)
class Case:
    name: str
    fix: str
    evidence: list[Evidence]
    used: bool  # the truth: did the agent apply this entry?


def code(text: str, path: str = "app.py") -> Evidence:
    return Evidence(text, path=path)


def run(command: str) -> Evidence:
    return Evidence(command)


CASES = [
    Case("rename applied", RENAME, [code("cache = CacheConfig(expire_after=60)")], used=True),
    Case("strict applied", STRICT, [code("rows = read_rows(text, strict=False)")], used=True),
    Case(
        "retries applied",
        RETRIES,
        [code('client = Client(transport={"retries": 3})', "client.py")],
        used=True,
    ),
    Case("pin applied", PIN, [run("pip install 'markupsafe<2.1' && pip check")], used=True),
    Case("pin in requirements", PIN, [code("markupsafe<2.1", "requirements.txt")], used=True),
    Case(
        "keycloak applied",
        KEYCLOAK,
        [
            run(
                "docker exec full-cosmo-keycloak-1 sh -c 'kcadm.sh update realms/cosmo -s "
                "sslRequired=NONE'"
            )
        ],
        used=True,
    ),
    Case(
        "metrics applied",
        METRICS,
        [code("GRAPHQL_METRICS_COLLECTOR_ENDPOINT=http://host.docker.internal:4005", ".env")],
        used=True,
    ),
    # 2026-10-31: a deploy check retrieved the Keycloak entry; the agent then wrote about it in
    # PROGRESS.md and ran the checks. v0 counted `re-run`, `--server` and the prose.
    Case(
        "keycloak deploy check",
        KEYCLOAK,
        [
            code(
                "Stop marked it partial from `re-run`, `--server` and sslRequired=external.",
                "PROGRESS.md",
            ),
            run("make check"),
            run("gh pr create --title 'docs: add T41' --body-file - --server"),
        ],
        used=False,
    ),
    Case(
        "metrics written up in docs",
        METRICS,
        [code("Set GRAPHQL_METRICS_COLLECTOR_ENDPOINT to the collector.", "docs/notes.md")],
        used=False,
    ),
    Case(
        "rename described in the readme",
        RENAME,
        [code("quickcache 2.0 renamed `ttl=` to `expire_after=`.", "README.md"), run("pytest")],
        used=False,
    ),
    Case("unrelated session", PIN, [run("ls -la"), run("pytest -q")], used=False),
    Case(
        "generic flags only",
        KEYCLOAK,
        [run("docker compose up -d --force; re-run the tests --user me")],
        used=False,
    ),
]


def v0(fix: str, evidence: list[Evidence]) -> bool:
    """The shipped-before-0036 rule: equal weights, all text counts."""
    artifacts = fix_artifacts(fix)
    text = "\n".join(item.text for item in evidence).lower()
    return bool(artifacts) and any(a in text for a in artifacts)


def v1(fix: str, evidence: list[Evidence]) -> bool:
    return observe_use(fix, evidence, count_artifacts(STORE)).used in {"yes", "partial"}


def precision_recall(predict: object) -> tuple[float, float]:
    assert callable(predict)
    predicted = [(predict(c.fix, c.evidence), c.used) for c in CASES]
    true_positive = sum(p and t for p, t in predicted)
    positives = sum(p for p, _ in predicted)
    actual = sum(t for _, t in predicted)
    return true_positive / positives, true_positive / actual


def test_v1_is_more_precise_than_v0_and_keeps_every_true_use() -> None:
    old_precision, old_recall = precision_recall(v0)
    new_precision, new_recall = precision_recall(v1)
    assert (round(old_precision, 2), old_recall) == (0.7, 1.0)
    assert (new_precision, new_recall) == (1.0, 1.0)


def test_each_labeled_case() -> None:
    wrong = [c.name for c in CASES if v1(c.fix, c.evidence) != c.used]
    assert wrong == []
