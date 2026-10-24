import pytest

from commons.privacy.secrets import redact

# Fake secrets with the right shapes. None of these are real credentials.
CASES = [
    ("key: sk-ant-api03-" + "a" * 40, "anthropic_key", "key: [REDACTED:anthropic_key]"),
    ("OPENAI=sk-proj-" + "B" * 30, "openai_key", None),
    ("token ghp_" + "c" * 36, "github_token", "token [REDACTED:github_token]"),
    ("AKIAABCDEFGHIJKLMNOP is the id", "aws_access_key", "[REDACTED:aws_access_key] is the id"),
    ("AIza" + "d" * 35, "google_api_key", "[REDACTED:google_api_key]"),
    ("xoxb-1234567890-abcdef", "slack_token", "[REDACTED:slack_token]"),
    ("sk_live_" + "e" * 24, "stripe_key", "[REDACTED:stripe_key]"),
    (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
        "jwt",
        "[REDACTED:jwt]",
    ),
    (
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123",
        "bearer_token",
        "Authorization: Bearer [REDACTED:bearer_token]",
    ),
    (
        "postgresql://admin:hunter22@db.internal:5432/app",
        "connection_string",
        "postgresql://[REDACTED:connection_string]@db.internal:5432/app",
    ),
    ('DB_PASSWORD="correcthorse"', "assigned_secret", 'DB_PASSWORD="[REDACTED:assigned_secret]"'),
    ("api_key = abcd1234efgh", "assigned_secret", "api_key = [REDACTED:assigned_secret]"),
    ("ping jane.doe@example.com", "email", "ping [REDACTED:email]"),
    (
        "File /Users/jdoe/proj/app.py line 3",
        "username_path",
        "File /Users/<user>/proj/app.py line 3",
    ),
    ("/home/alice/.cache/pip", "username_path", "/home/<user>/.cache/pip"),
    (r"C:\Users\Bob\AppData", "username_path", r"C:\Users\<user>\AppData"),
]


@pytest.mark.parametrize(("text", "kind", "expected"), CASES)
def test_each_kind_is_redacted(text: str, kind: str, expected: str | None) -> None:
    result = redact(text)
    assert dict(result.counts) == {kind: 1}
    if expected is not None:
        assert result.text == expected


def test_private_key_block_is_removed_entirely() -> None:
    text = (
        "before\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----\nafter"
    )
    result = redact(text)
    assert result.text == "before\n[REDACTED:private_key]\nafter"
    assert dict(result.counts) == {"private_key": 1}


@pytest.mark.parametrize(
    "text",
    [
        "ImportError: cannot import name 'soft_unicode' from 'markupsafe'",
        "pip install 'markupsafe<2.1' && git clone git@github.com:org/repo.git",
        "NODE_OPTIONS=--openssl-legacy-provider npm run build",
        "postgresql://localhost:5432/commons",
        "/Users/<user>/project and /home/runner/work",
        "max_tokens = 4096",
    ],
)
def test_ordinary_debugging_text_is_untouched(text: str) -> None:
    result = redact(text)
    assert result.text == text
    assert not result.counts


def test_multiple_findings_are_counted() -> None:
    result = redact("a@example.com and b@example.org at /Users/carol/x")
    assert dict(result.counts) == {"email": 2, "username_path": 1}


def test_a_redacted_value_is_not_redacted_again() -> None:
    result = redact("GITHUB_TOKEN=ghp_" + "x" * 36)
    assert result.text == "GITHUB_TOKEN=[REDACTED:github_token]"
    assert dict(result.counts) == {"github_token": 1}
