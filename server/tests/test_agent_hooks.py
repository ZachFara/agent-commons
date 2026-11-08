"""The Claude Code hooks that fence in the agent workflow (.claude/hooks, tools/agent_hooks).

Each hook's `main()` runs in-process on a recorded-shape event. git is real (a minimal
repository on disk); `gh` is a stub on PATH, since GitHub can't run locally.
"""

import io
import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from tools.agent_hooks import bash_guard, linear_guard, record_issue

REPO = Path(__file__).resolve().parents[2]
COMMONS_ISSUE = "MIT-1"  # stands for any issue in Zach's read-only Commons project
UUID = "6a5cb50e-7d88-4e23-b46a-442a7277c540"


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "agents"
    monkeypatch.setenv("AGENTS_STATE_DIR", str(directory))
    return directory


def run(main: Callable[[], int], event: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> int:
    """Feed `event` to a hook's main() on stdin; return its exit code."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    return main()


def linear(tool: str, arguments: dict[str, object], **extra: object) -> dict[str, object]:
    return {"tool_name": f"mcp__claude_ai_Linear__{tool}", "tool_input": arguments, **extra}


AGENT_PROJECT = {"team": "MIT", "project": "Commons Agent Project"}

ALLOWED_LINEAR = [
    linear("get_issue", {"id": COMMONS_ISSUE}),
    linear("list_issues", {"project": "Commons", "state": "Delegated"}),
    linear("list_comments", {"issueId": COMMONS_ISSUE}),
    linear("save_issue", {**AGENT_PROJECT, "title": "Question: which index?"}),
    linear("save_issue", {"team": "MIT", "project": "P-MIT-29", "title": "t"}),
    linear("save_issue", {**AGENT_PROJECT, "title": "t", "parentId": "MIT-580"}),
    linear("save_issue", {**AGENT_PROJECT, "title": "t", "blockedBy": ["mit-580"]}),
    linear("save_issue", {"id": "MIT-580", "state": "In Progress"}),
    linear("save_issue", {"id": "MIT-580", "project": "P-MIT-29"}),
    linear("save_comment", {"issueId": "MIT-580", "body": "Blocked: tests need Docker"}),
    {"tool_name": "mcp__linear__save_issue", "tool_input": {"id": "MIT-580", "state": "Done"}},
]

DENIED_LINEAR = [
    linear("save_issue", {"team": "MIT", "project": "Commons", "title": "t"}),
    linear("save_issue", {"team": "MIT", "title": "no project"}),
    linear("save_issue", {**AGENT_PROJECT, "title": "t", "parentId": COMMONS_ISSUE}),
    linear("save_issue", {**AGENT_PROJECT, "title": "t", "relatedTo": [COMMONS_ISSUE]}),
    linear("save_issue", {"id": COMMONS_ISSUE, "state": "Done"}),
    linear("save_issue", {"id": "MIT-580", "project": "Commons"}),
    linear("save_issue", {"id": "MIT-580", "project": None}),
    linear("save_issue", {"id": "MIT-580", "duplicateOf": COMMONS_ISSUE}),
    linear("save_issue", {"id": "MIT-580", "removeBlocks": [COMMONS_ISSUE]}),
    linear("save_comment", {"issueId": COMMONS_ISSUE, "body": "hi"}),
    linear("save_comment", {"projectId": "P-MIT-27", "body": "hi"}),
    linear("save_comment", {"issueId": "MIT-580", "parentId": "c1", "body": "reply"}),
    linear("save_comment", {"id": "c1", "body": "edit"}),
    linear("delete_comment", {"id": "c1"}),
    linear("save_project", {"id": "P-MIT-27", "name": "x"}),
    linear("create_attachment", {"issueId": "MIT-580"}),
    linear("share_issue", {"id": "MIT-580"}),
    linear("get_issue", {"id": "MIT-580"}, agent_type="coder"),
    {"tool_name": "mcp__linear__save_issue", "tool_input": {"id": COMMONS_ISSUE}},
]


@pytest.mark.parametrize("event", ALLOWED_LINEAR)
def test_linear_guard_allows(event: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(linear_guard.main, event, monkeypatch) == 0


@pytest.mark.parametrize("event", DENIED_LINEAR)
def test_linear_guard_denies_with_a_reason(
    event: dict[str, object], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(linear_guard.main, event, monkeypatch) == 2
    assert capsys.readouterr().err.startswith("Blocked: ")


def test_owned_list_is_seeded(state_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run(linear_guard.main, linear("save_issue", {"id": "MIT-580"}), monkeypatch)
    assert (state_dir / "owned-issues.txt").read_text(encoding="utf-8") == "MIT-580\n"


def created(response: object, arguments: dict[str, object] | None = None) -> dict[str, object]:
    return linear(
        "save_issue", arguments or {**AGENT_PROJECT, "title": "t"}, tool_response=response
    )


ISSUE = {"id": "MIT-601", "uuid": UUID, "title": "t", "project": "Commons Agent Project"}
RESPONSES = [
    [{"type": "text", "text": json.dumps(ISSUE)}],
    {"content": [{"type": "text", "text": json.dumps(ISSUE)}], "isError": False},
    json.dumps(ISSUE),
    ISSUE,
    {"issue": {"identifier": "MIT-601", "id": UUID, "parent": {"id": "MIT-580"}}},
]


@pytest.mark.parametrize("response", RESPONSES)
def test_record_issue_then_agents_may_update_it(
    response: object, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run(record_issue.main, created(response), monkeypatch) == 0
    owned = (state_dir / "owned-issues.txt").read_text(encoding="utf-8").split()
    assert owned == ["MIT-580", "MIT-601", UUID]
    for issue in ("MIT-601", UUID):
        update = linear("save_issue", {"id": issue, "state": "Done"})
        assert run(linear_guard.main, update, monkeypatch) == 0


def test_record_issue_ignores_updates_and_repeats(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(record_issue.main, created(ISSUE), monkeypatch)
    run(record_issue.main, created(ISSUE), monkeypatch)
    update = created({"id": "MIT-700"}, {"id": "MIT-700", "state": "Done"})
    assert run(record_issue.main, update, monkeypatch) == 0
    owned = (state_dir / "owned-issues.txt").read_text(encoding="utf-8").split()
    assert owned == ["MIT-580", "MIT-601", UUID]


def test_record_issue_fails_loudly_on_an_unknown_shape(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(record_issue.main, created([{"type": "text", "text": "Created!"}]), monkeypatch) == 2
    assert "Created!" in (state_dir / "record_issue-unparsed.json").read_text(encoding="utf-8")
    assert "Add it by hand" in capsys.readouterr().err


# ------------------------------------------------------------------------------ bash_guard

AGENT_TICKET = "MIT-601"  # an agent-created issue: owned, so it may appear in GitHub text


@pytest.fixture
def agent_ticket(state_dir: Path) -> None:
    """Own AGENT_TICKET besides the seed, as record_issue would after creating it."""
    state_dir.mkdir()
    (state_dir / "owned-issues.txt").write_text(f"MIT-580\n{AGENT_TICKET}\n", encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal git repository on disk; git's own commands read it."""
    root = tmp_path / "repo"
    for directory in ("objects", "refs/heads", "refs/tags"):
        (root / ".git" / directory).mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return root


def checkout(repo: Path, branch: str) -> None:
    (repo / ".git" / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")


@pytest.fixture
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `gh` stub that answers `gh pr view` with $FAKE_PR_BASE and logs its arguments."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh-args"
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        '[ -n "$FAKE_PR_BASE" ] || { echo "no pull requests found" >&2; exit 1; }\n'
        'echo "$FAKE_PR_BASE"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path('/usr/bin')}:{Path('/bin')}")
    return log


def bash(command: str, repo: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    event: dict[str, object] = {"tool_name": "Bash", "tool_input": {"command": command}}
    event["cwd"] = str(repo)
    return run(bash_guard.main, event, monkeypatch)


ALLOWED_BASH = [
    "git status && git log --oneline -20",
    "git push -u origin feat/MIT-601",
    "git push origin feature/search:feature/search",
    "git push origin stable/20261107T221500Z",
    "git push --delete origin feat/MIT-601",
    'git commit -m "fix: handle -n and --force in names"',
    "git commit -am 'feat: x'",
    "git -C ../other commit -m 'chore: y'",
    "gh pr create --base main --title 'MIT-601: search' --body \"Uses `rank()`\"",
    "gh pr view 12 --json baseRefName",
    "gh api repos/ZachFara/agent-commons/pulls/12",
    "gh api -X GET repos/ZachFara/agent-commons/pulls -f state=open",
    "gh api graphql -f query='{ viewer { login } }'",
    "uv run pytest server/tests/test_smoke.py 2>&1 | tail -5",
    "git commit -F - <<'EOF'\ndocs: never git push --force to main\nEOF",
    "gh pr create --base feature/MIT-12-x --body \"$(cat <<'EOF'\n## Learned\nSKIP= no\nEOF\n)\"",
]

DENIED_BASH = [
    "git push origin main",
    "git push origin HEAD:main",
    "git push origin feat/x:refs/heads/main",
    "git push --delete origin main",
    "git push origin :main",
    "git push -f origin feat/x",
    "git push --force-with-lease origin feat/x",
    "git push -uf origin feat/x",
    "git push origin +feat/x",
    "git push --mirror origin",
    "git push --all origin",
    "git status; git push --force origin feat/x",
    "git status\ngit push --force origin feat/x",
    "make check && git push origin main",
    "echo $(git push origin main)",
    "bash -c 'git push --force origin feat/x'",
    'sh -lc "git push origin main"',
    "bash <<'EOF'\ngit push origin main\nEOF",
    "eval git push origin main",
    "echo feat/x | xargs git push -f origin",
    "/usr/bin/git push origin main",
    "env GIT_TRACE=1 git push origin main",
    "git commit --no-verify -m 'x'",
    "git commit -nm 'x'",
    "git commit -m 'x' -n",
    "git -c core.hooksPath=/dev/null commit -m x",
    "SKIP=check git commit -m x",
    "gh pr merge 12 --squash --admin",
    "gh api -X PUT repos/ZachFara/agent-commons/branches/main/protection",
    "gh api --method=DELETE repos/ZachFara/agent-commons/rulesets/1",
    "gh api repos/ZachFara/agent-commons -f visibility=private",
    "gh api graphql -f query='mutation { deleteBranchProtectionRule(input: {}) { x } }'",
    "gh repo edit --default-branch dev",
    "gh secret set TOKEN",
    "gh variable delete GCP_OPERATIONS",
    "gcloud secrets versions access latest --secret agent-commons-production-database-url",
    "cat ~/.config/agent-commons/config.json",
    "git commit -m 'unbalanced",
]


@pytest.mark.usefixtures("gh", "agent_ticket")
@pytest.mark.parametrize("command", ALLOWED_BASH)
def test_bash_guard_allows(command: str, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout(repo, "feat/MIT-601")
    assert bash(command, repo, monkeypatch) == 0


@pytest.mark.usefixtures("gh")
@pytest.mark.parametrize("command", DENIED_BASH)
def test_bash_guard_denies_with_a_reason(
    command: str,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKE_PR_BASE", "feature/MIT-12-search")
    checkout(repo, "feat/MIT-601")
    assert bash(command, repo, monkeypatch) == 2
    assert capsys.readouterr().err.startswith("Blocked: ")


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize("command", ["git push", "git push -u origin HEAD", "git push origin"])
def test_bash_guard_denies_a_bare_push_from_main(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert bash(command, repo, monkeypatch) == 2
    checkout(repo, "feat/MIT-601")
    assert bash(command, repo, monkeypatch) == 0


def test_bash_guard_ignores_git_variables_from_a_git_hook(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside a pre-commit check git sets GIT_DIR; the guard must still read `cwd`'s repo."""
    other = tmp_path / "other"
    for directory in ("objects", "refs/heads"):
        (other / ".git" / directory).mkdir(parents=True)
    (other / ".git" / "HEAD").write_text("ref: refs/heads/feat/x\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert bash("git push", repo, monkeypatch) == 2  # repo is on main


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    ("command", "selector"),
    [
        ("gh pr merge 12 --squash --delete-branch", "12"),
        ("gh pr merge --squash --subject 'MIT-601: x (#12)' 12", "12"),
        ("gh pr merge https://github.com/o/r/pull/12 -s", "https://github.com/o/r/pull/12"),
        ("gh pr merge -R ZachFara/agent-commons 12 --squash", "12 --repo ZachFara/agent-commons"),
    ],
)
def test_bash_guard_merges_only_into_feature_branches(
    command: str, selector: str, repo: Path, gh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_PR_BASE", "feature/MIT-12-search")
    assert bash(command, repo, monkeypatch) == 0
    assert gh.read_text(encoding="utf-8").startswith(f"pr view {selector} --json baseRefName")
    for base in ("main", "feat/MIT-601", "release/feature/x"):
        monkeypatch.setenv("FAKE_PR_BASE", base)
        assert bash(command, repo, monkeypatch) == 2
    monkeypatch.delenv("FAKE_PR_BASE")  # gh pr view fails: deny
    assert bash(command, repo, monkeypatch) == 2


# Commands that put an issue ID ({id}) into GitHub-visible text. Linear's GitHub integration
# links a PR to every issue its title, body or head branch names, and moves that issue.
FOREIGN_ID = "MIT-575"  # stands for any issue agents didn't create, e.g. one of Zach's
ID_COMMANDS = [
    "git commit -m 'feat: queue badge ({id})'",
    "git commit -am 'fix: {id}'",
    "git commit --message='chore: x' --message 'Source: {id}'",
    "git commit -m 'feat: x' -m 'Refs {id}'",
    "git commit -F - <<'EOF'\nfeat: x\n\nRefs {id}\nEOF",
    "git commit -m \"$(cat <<'EOF'\nfeat: x for {id}\nEOF\n)\"",
    "gh pr create --base feature/search --title '{id}: queue badge' --body x",
    "gh pr create --base feature/search -t x -b 'closes {id}'",
    "gh pr create --base feature/search --title x --body \"$(cat <<'EOF'\nSee {id}\nEOF\n)\"",
    "gh pr create --base main --head feature/{id}-queue-badge --title x",
    "gh pr create --base main -H feat/{id} --title x",
    "gh pr edit 12 --title '{id}: queue badge'",
    "gh pr edit 12 -b 'about {id}'",
    "gh pr merge 12 --squash --subject '{id}: x (#12)'",
    "git switch -c feature/{id}-queue-badge",
    "git switch -C feat/{id} origin/main",
    "git switch --create=feat/{id}",
    "git checkout -b feat/{id}",
    "git checkout -qB feat/{id} origin/main",
    "git branch feat/{id}",
    "git branch -m feat/x feat/{id}",
    "git push -u origin feat/{id}",
    "git push --set-upstream origin feat/{id}",
    "git push origin origin/main:refs/heads/feature/{id}-queue-badge",
    "git push origin feat/{id}:feat/x",
    "git -C sub commit -m 'x {id}'",
    "bash -c \"git commit -m 'x {id}'\"",
]
DENIAL = "write it without the ID (Linear links PR text to that ticket)"


@pytest.mark.usefixtures("gh", "agent_ticket")
@pytest.mark.parametrize("template", ID_COMMANDS)
def test_bash_guard_denies_issue_ids_agents_dont_own(
    template: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FAKE_PR_BASE", "feature/search")
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(template.format(id=AGENT_TICKET), repo, monkeypatch) == 0
    assert bash(template.format(id=FOREIGN_ID), repo, monkeypatch) == 2
    err = capsys.readouterr().err
    assert FOREIGN_ID in err
    assert DENIAL in err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    ("allowed", "denied"),
    [
        ("git switch -c feat/mit-601-x", "git switch -c feat/mit-575-x"),
        ("git commit -m 'Mit-601'", "git commit -m 'Mit-575'"),
        ("git commit -m 'feature/MIT-601-x'", "git commit -m 'feature/MIT-575-x'"),
        ("git commit -m 'in P-MIT-29'", "git commit -m 'in MIT-29'"),
        ("git switch -c fix/submit-2-forms", "git switch -c fix/x_MIT-575"),
    ],
)
def test_bash_guard_finds_ids_in_any_case_and_inside_words(
    allowed: str, denied: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert bash(allowed, repo, monkeypatch) == 0
    assert bash(denied, repo, monkeypatch) == 2


@pytest.mark.usefixtures("gh")
@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --base feature/MIT-575-queue-badge --head feat/MIT-580 --title x",
        "gh pr edit 12 --base feature/MIT-575-queue-badge",
        "git switch feature/MIT-575-queue-badge",
        "git fetch origin feature/MIT-575-queue-badge",
        "git branch -D feature/MIT-575-queue-badge",
        "git log --oneline origin/main..origin/feature/MIT-575-queue-badge",
    ],
)
def test_bash_guard_allows_existing_branches_and_bases(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Linear doesn't link a PR's base branch; reading or switching to a branch writes nothing."""
    assert bash(command, repo, monkeypatch) == 0


@pytest.mark.usefixtures("agent_ticket")
def test_bash_guard_denies_pushing_the_current_branch_with_an_id(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(repo, f"feature/{FOREIGN_ID}-queue-badge")
    assert bash("git push -u origin HEAD", repo, monkeypatch) == 2
    assert bash("git push", repo, monkeypatch) == 2


@pytest.mark.usefixtures("gh", "agent_ticket")
@pytest.mark.parametrize(
    "template",
    [
        "git commit -F {path}",
        "git commit -aF{path}",
        "git commit --file={path}",
        "git commit --file {path}",
        "git -C .scratch commit -F msg.txt",
        "gh pr create --base feature/search --title x --body-file {path}",
        "gh pr edit 12 -F {path}",
    ],
)
def test_bash_guard_reads_message_files_from_disk(
    template: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    message = repo / ".scratch" / "msg.txt"
    message.parent.mkdir()
    command = template.format(path=".scratch/msg.txt")  # relative to the command's directory
    message.write_text(f"feat: x\n\nPart of {AGENT_TICKET}.\n", encoding="utf-8")
    assert bash(command, repo, monkeypatch) == 0
    message.write_text(f"feat: x\n\nPart of {FOREIGN_ID}.\n", encoding="utf-8")
    assert bash(command, repo, monkeypatch) == 2
    assert FOREIGN_ID in capsys.readouterr().err
    message.unlink()
    assert bash(command, repo, monkeypatch) == 2
    assert "can't read" in capsys.readouterr().err


# ------------------------------------------------------------------------------- wiring


def test_settings_wire_each_hook_to_an_entry_script() -> None:
    settings = json.loads((REPO / ".claude/settings.json").read_text(encoding="utf-8"))
    wired = {
        (event, group["matcher"], hook["command"])
        for event, groups in settings["hooks"].items()
        for group in groups
        for hook in group["hooks"]
    }
    linear_tools = "mcp__(claude_ai_Linear|linear)__"
    assert wired == {
        ("PreToolUse", f"{linear_tools}.*", '"$CLAUDE_PROJECT_DIR"/.claude/hooks/linear_guard.sh'),
        ("PreToolUse", "Bash", '"$CLAUDE_PROJECT_DIR"/.claude/hooks/bash_guard.sh'),
        (
            "PostToolUse",
            f"{linear_tools}save_issue",
            '"$CLAUDE_PROJECT_DIR"/.claude/hooks/record_issue.sh',
        ),
    }
    assert re.search(f"^{linear_tools}.*", "mcp__claude_ai_Linear__save_comment")
    hooks = [hook for groups in settings["hooks"].values() for g in groups for hook in g["hooks"]]
    assert all(hook["onFailure"] == "block" for hook in hooks)  # a missing script blocks too
    for name in ("linear_guard", "bash_guard", "record_issue"):
        script = REPO / ".claude/hooks" / f"{name}.sh"
        assert script.stat().st_mode & 0o111, f"{script} is not executable"
        text = script.read_text(encoding="utf-8")
        assert f"python -m tools.agent_hooks.{name} || exit 2" in text  # fails closed
