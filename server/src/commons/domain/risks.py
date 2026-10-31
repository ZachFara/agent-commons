"""What the risk scanner's findings are called where people and agents read them (0029)."""

RISK_LABELS = {
    "pipe_to_shell": "pipe-to-shell",
    "destructive_delete": "deletes broad paths",
    "credential_access": "reads credentials",
    "security_disabled": "turns off security",
    "encoded_exec": "runs decoded text",
    "unpinned_install": "installs from a raw URL",
    "agent_directed": "addresses the reading agent",
    "invisible_unicode": "hidden characters",
}


def risk_labels(report: object) -> list[str]:
    """Return the distinct labels in a stored risk report, for provenance."""
    if not isinstance(report, dict):
        return []
    findings = report.get("findings")
    if not isinstance(findings, list):
        return []
    kinds = {str(f.get("kind")) for f in findings if isinstance(f, dict)}
    return sorted(RISK_LABELS.get(kind, kind) for kind in kinds)
