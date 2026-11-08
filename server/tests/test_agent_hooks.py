"""The Claude Code hooks that fence in the agent workflow (.claude/hooks, tools/agent_hooks).

Each hook's `main()` runs in-process on a recorded-shape event. git is real (a minimal
repository on disk); `gh` is a stub on PATH, since GitHub can't run locally.
"""

import io
import json
import os
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
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal git repository on disk; git's own commands read it, and no other config."""
    root = tmp_path / "repo"
    for directory in ("objects", "refs/heads", "refs/tags"):
        (root / ".git" / directory).mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
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
    "gh pr create --base main --title 'MIT-601: search' --body 'Uses rank()'",
    "gh pr view 12 --json baseRefName",
    "gh api repos/ZachFara/agent-commons/pulls/12",
    "gh api -X GET repos/ZachFara/agent-commons/pulls -f state=open",
    "gh api -XGET repos/ZachFara/agent-commons/pulls -f state=open",
    "git config --get alias.ci",
    "git config --unset alias.ci",
    "gh api graphql -f query='{ viewer { login } }'",
    "uv run pytest server/tests/test_smoke.py 2>&1 | tail -5",
    "git commit -F - <<'EOF'\ndocs: never git push --force to main\nEOF",
    "gh pr create --base feature/MIT-12-x -t x --body \"$(cat <<'EOF'\n## Learned\nSKIP=\nEOF\n)\"",
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
    "gh api -XPOST repos/ZachFara/agent-commons/issues",
    "gh api -XDELETE repos/ZachFara/agent-commons/rulesets/1",
    "gh api -X=PATCH repos/ZachFara/agent-commons",
    "gh api graphql -F query=@.scratch/q.graphql",
    "gh api graphql -f query=@.scratch/q.graphql",
    "gh api graphql --input .scratch/q.json",
    "gh api graphql --input=.scratch/q.json",
    "git -c alias.ci=commit ci -m x",
    "git -c alias.p='!git push --force' p",
    "git -c Alias.st=status st",
    "git --config-env alias.p=CMD p",
    "git --config-env=alias.p=CMD p",
    "git config alias.p '!git push --force'",
    "git config --global alias.ci commit",
    "git config set alias.ci commit",
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
@pytest.mark.parametrize(
    "command", ["git push", "git push -u origin HEAD", "git push origin", "git push origin @"]
)
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
    "gh pr create --base main --head feature/{id}-queue-badge --title x --body y",
    "gh pr create --base main -H feat/{id} -t x -b y",
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
    "git worktree add -b feat/{id} ../wt origin/main",
    "git worktree add -B feat/{id} ../wt",
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
        ("git switch -c fix/submit-2-forms", "git switch -c fix/x_MIT-575"),
    ],
)
def test_bash_guard_finds_ids_in_any_case_and_inside_words(
    allowed: str, denied: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert bash(allowed, repo, monkeypatch) == 0
    assert bash(denied, repo, monkeypatch) == 2


def test_bash_guard_denies_a_project_id_too(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Linear may link the issue inside `P-MIT-29`; the project has a name."""
    assert bash("git commit -m 'feat: x for P-MIT-29'", repo, monkeypatch) == 2
    assert "name the project by name" in capsys.readouterr().err
    assert bash("git commit -m 'feat: x for Commons Agent Project'", repo, monkeypatch) == 0


@pytest.mark.usefixtures("gh")
@pytest.mark.parametrize(
    "command",
    [
        # The final PR into main comes from final/<slug>, cut from an older ID-named branch.
        "git worktree add -b final/queue-badge ../final origin/feature/MIT-575-queue-badge",
        "git branch final/queue-badge origin/feature/MIT-575-queue-badge",
        "git push -u origin final/queue-badge",
        "git push origin 1a2b3c4d:refs/heads/final/queue-badge",
        "gh pr create --base main --head final/queue-badge --title x --body-file .scratch/x.md",
        # The base branch isn't linked; reading or switching to a branch writes nothing.
        "gh pr create --base feature/MIT-575-queue-badge --head feat/MIT-580 --title x --body y",
        "gh pr edit 12 --base feature/MIT-575-queue-badge",
        "gh pr merge 12 --squash --delete-branch",
        "git switch feature/MIT-575-queue-badge",
        "git fetch origin feature/MIT-575-queue-badge",
        "git branch -D feature/MIT-575-queue-badge",
        "git log --oneline origin/main..origin/feature/MIT-575-queue-badge",
        "git diff origin/main...origin/feature/MIT-575-queue-badge",
    ],
)
def test_bash_guard_allows_final_branches_and_existing_bases(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_PR_BASE", f"feature/{FOREIGN_ID}-queue-badge")
    (repo / ".scratch").mkdir()
    (repo / ".scratch" / "x.md").write_text("Adds the queue badge.\n", encoding="utf-8")
    assert bash(command, repo, monkeypatch) == 0


@pytest.mark.usefixtures("gh", "agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        "git push -u origin HEAD",
        "git push",
        "git push origin @",
        "gh pr create --base main --title x --body y",
    ],
)
def test_bash_guard_checks_the_current_branch_when_it_is_pushed_or_the_head(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout(repo, f"feature/{FOREIGN_ID}-queue-badge")
    assert bash(command, repo, monkeypatch) == 2
    assert FOREIGN_ID in capsys.readouterr().err
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 0


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --base main --fill",
        "gh pr create --base main -f",
        "gh pr create --base main -df",
        "gh pr create --base main --fill-first",
        "gh pr create --base main --fill-verbose",
        "gh pr create --base main --title x --body y --fill",
        "gh pr create --base main --title x",
        "gh pr create --base main --body y",
        "gh pr create --base main --body-file .scratch/x.md",
        "gh pr create --base main --web",
    ],
)
def test_bash_guard_wants_an_explicit_pr_title_and_body(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert "explicit --title and --body or --body-file" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        "printf 'body' | gh pr create --base main --title x --body-file -",
        "gh pr create --base main --title x -F - < .scratch/x.md",
        "gh pr edit 12 --body-file - < .scratch/x.md",
        "git commit -F - < .scratch/x.md",
        'git commit -F - <<< "feat: x"',
        "cat .scratch/x.md | git commit --file=-",
    ],
)
def test_bash_guard_denies_stdin_messages_without_a_heredoc(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert "pass --body-file <path> or a heredoc" in capsys.readouterr().err


@pytest.mark.usefixtures("gh", "agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "$(git log -1 --format=%s)"',
        'git commit -m "feat: $TITLE"',
        "git commit -m $MSG",
        "git commit -m $(cat .scratch/x.md)",
        "git commit -m '${TITLE}'",
        'gh pr create --base main --title x --body "Uses `rank()`"',
        'gh pr create --base main --title "$(git log -1 --format=%s)" --body y',
        'gh pr edit 12 --title "$T"',
        'gh pr merge 12 --squash --subject "$(cat .scratch/x.md)"',
        "git commit -F - <<EOF\nfeat: $(date)\nEOF",
        'gh pr create --base main --title x --body "$(cat <<EOF\nSee $URL\nEOF\n)"',
    ],
)
def test_bash_guard_denies_shell_substitution_in_messages(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FAKE_PR_BASE", "feature/search")
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert "--body-file <path>" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
def test_bash_guard_expands_configured_git_aliases(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / ".git" / "config").write_text(
        "[alias]\n"
        "\tci = commit\n"
        "\tnew = switch -c\n"
        "\tup = !git push --force origin HEAD\n"
        "\tci2 = ci\n",
        encoding="utf-8",
    )
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(f"git ci -m 'feat: {AGENT_TICKET}'", repo, monkeypatch) == 0
    assert bash(f"git new feat/{AGENT_TICKET}-x", repo, monkeypatch) == 0
    assert bash("git status", repo, monkeypatch) == 0
    assert bash(f"git ci -m 'feat: {FOREIGN_ID}'", repo, monkeypatch) == 2
    assert bash("git ci -nm 'feat: x'", repo, monkeypatch) == 2
    assert bash(f"git new feature/{FOREIGN_ID}-x", repo, monkeypatch) == 2
    capsys.readouterr()
    for shell_or_nested in ("git up", "git ci2 -m x"):
        assert bash(shell_or_nested, repo, monkeypatch) == 2
        assert "is an alias for" in capsys.readouterr().err


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


@pytest.fixture
def other_repo(tmp_path: Path) -> Path:
    """A second repository whose config holds an alias the working repo doesn't have."""
    other = tmp_path / "other"
    for directory in ("objects", "refs/heads"):
        (other / ".git" / directory).mkdir(parents=True)
    (other / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (other / ".git" / "config").write_text(
        "[alias]\n\tup = !git push --force origin HEAD\n", encoding="utf-8"
    )
    return other


@pytest.mark.usefixtures("agent_ticket", "other_repo")
@pytest.mark.parametrize(
    "command",
    [
        "git -c include.path=../other/.git/config up",
        "git -c includeIf.gitdir:/.path=../other/.git/config up",
        "git -c include.path=../other/.git/config status",  # it may set core.hooksPath
        "git --config-env=include.path=CONFIG commit -m 'feat: x'",
        "git config include.path ../other/.git/config",
        "git config --add includeIf.onbranch:feat/x.path ../other/.git/config",
        "git config set include.path ../other/.git/config",
        "git --git-dir=../other/.git up",
        "git --git-dir ../other/.git up",
        "git --work-tree=../other up",
        "git --bare up",
        "GIT_DIR=../other/.git git up",
        "GIT_CONFIG_PARAMETERS=\"'alias.up=!git push --force origin HEAD'\" git up",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.up GIT_CONFIG_VALUE_0='!sh' git up",
        "GIT_CONFIG_GLOBAL=../other/.git/config git up",
        "env HOME=../other git up",
        "export GIT_CONFIG_GLOBAL=../other/.git/config; git up",
        "GIT_DIR=../other/.git bash -c 'git up'",
        "XDG_CONFIG_HOME=../other git -C . up",
    ],
)
def test_bash_guard_denies_aliases_from_config_it_cant_read(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert capsys.readouterr().err.startswith("Blocked: ")


@pytest.mark.usefixtures("agent_ticket", "other_repo")
@pytest.mark.parametrize(
    "command",
    [
        "git --git-dir=../other/.git log --oneline -5",  # builtins: no alias can shadow them
        "GIT_DIR=../other/.git git status",
        "HOME=../other git diff",
        "git -c user.name=x commit -m 'feat: x'",
        "git config --get include.path",
        "git config --unset include.path",
    ],
)
def test_bash_guard_allows_builtins_and_reading_includes(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 0


@pytest.mark.parametrize(
    "command",
    [
        'gh api graphql -f query="$Q"',
        "gh api graphql -f query=$(cat .scratch/q.graphql)",
        "gh api graphql -F query=`cat .scratch/q.graphql`",
        "gh api graphql -fquery=${Q}",
        "gh api graphql -f query='{ viewer { login } }' -f login=$LOGIN",
    ],
)
def test_bash_guard_denies_substitution_in_graphql(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bash(command, repo, monkeypatch) == 2
    assert "gh api graphql uses shell substitution" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        # A heredoc elsewhere on the line doesn't feed this command.
        "cat <<'EOF' > /dev/null\nfeat: x\nEOF\ncat .scratch/x.md | git commit -F -",
        "cat <<'EOF'\nfeat: x\nEOF\ngh pr edit 12 --body-file - < .scratch/x.md",
        # The heredoc feeds cat; git commit reads the pipe.
        "cat <<'EOF' | git commit -F -\nfeat: x\nEOF",
        # A pipe feeds the command, or a later redirection replaces the heredoc.
        "printf 'feat: x' | git commit -F - <<'EOF'\nfeat: x\nEOF",
        "git commit -F - <<'EOF' < .scratch/x.md\nfeat: x\nEOF",
        # A typed `<<heredoc` with no end isn't a heredoc the guard has read.
        "cat <<'EOF'\nx\nEOF\ngit commit -F - <<heredoc\nfeat: x",
    ],
)
def test_bash_guard_wants_the_heredoc_on_the_command_that_reads_stdin(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert "pass --body-file <path> or a heredoc" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
def test_bash_guard_checks_the_rest_of_a_heredoc_line(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    body = "--body-file - <<'EOF'\nAdds the badge.\nEOF"
    assert bash(f"gh pr create --base main --title x {body}", repo, monkeypatch) == 0
    for rest in ("&& git push origin main", "; git push --force origin feat/x"):
        assert bash(f"git commit -F - <<'EOF' {rest}\nfeat: x\nEOF", repo, monkeypatch) == 2
        assert "push" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        'gh pr create --base main --head "$BRANCH" --title x --body y',
        "gh pr create --base main --title x --body y --head=$(git branch --show-current)",
        "gh pr create --base main -t x -b y -H `git branch --show-current`",
        'git push origin "$BRANCH"',
        "git push -u origin feat/x:$TARGET",
        "git push origin HEAD:$(echo main)",
        "git push origin `echo main`",
        "git push $REMOTE",
        "git commit -m `cat .scratch/x.md`",
        "gh pr edit 12 --title=x`cat .scratch/x.md`",
    ],
)
def test_bash_guard_denies_substitution_in_heads_and_pushes(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert "shell substitution" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
def test_bash_guard_allows_backticks_outside_checked_text(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash("echo `git rev-parse --show-toplevel`", repo, monkeypatch) == 0
    assert bash("echo `git rev-parse HEAD`; git push -u origin HEAD", repo, monkeypatch) == 0


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "path",
    ["/dev/null", "/dev/zero", ".scratch", ".scratch/fifo", ".scratch/zero", "~no-such-user/x.md"],
)
@pytest.mark.parametrize("template", ["git commit -F {path}", "gh pr edit 12 --body-file {path}"])
def test_bash_guard_reads_only_regular_message_files(
    template: str,
    path: str,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scratch = repo / ".scratch"
    scratch.mkdir()
    os.mkfifo(scratch / "fifo")  # nothing writes it: a blocking read would hang the guard
    (scratch / "zero").symlink_to("/dev/zero")
    assert bash(template.format(path=path), repo, monkeypatch) == 2
    assert "can't read" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
def test_bash_guard_caps_the_message_file_it_reads(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    message = repo / ".scratch" / "msg.txt"
    message.parent.mkdir()
    message.write_text("feat: x\n" + "a" * (bash_guard.MAX_MESSAGE_BYTES - 8), encoding="utf-8")
    assert bash("git commit -F .scratch/msg.txt", repo, monkeypatch) == 0
    message.write_text("feat: x\n" + "a" * bash_guard.MAX_MESSAGE_BYTES, encoding="utf-8")
    assert bash("git commit -F .scratch/msg.txt", repo, monkeypatch) == 2
    assert "larger than 1 MiB" in capsys.readouterr().err


@pytest.mark.usefixtures("gh", "agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        # Shell keywords come before the program.
        "{ git push origin main; }",
        "if true; then git push --force origin feat/x; fi",
        "if false; then :; else git push origin main; fi",
        "if false; then :; elif git push origin main; then :; fi",
        "for b in x; do git push --force origin feat/x; done",
        "while true; do git push origin main; done",
        "until git push origin main; do :; done",
        "! git push origin main",
        "! { git push origin main; }",
        # Wrapper options, and the values some of them take.
        "env -u X git push origin main",
        "env -i PATH=/usr/bin git push origin main",
        "env --unset=X git push origin main",
        "env --unset X git push origin main",
        "env -C . git push origin main",
        "env -- git push origin main",
        "sudo -u x git push origin main",
        "sudo -E -u x git push origin main",
        "sudo -Eux git push origin main",
        "sudo --user=x git push origin main",
        "command -p git push origin main",
        "exec -a x git push origin main",
        "time -p git push origin main",
        # env -S splits its value into the command it runs.
        "env -S 'git push origin main'",
        "env -S'git push --force origin feat/x'",
        "env --split-string='git push origin main'",
        "env -i -S 'git push origin main'",
        # eval joins its arguments into a command line and runs that.
        'eval "git push origin main"',
        "eval 'git push --force origin feat/x'",
        "eval 'git' 'push origin main'",
        "eval 'make check && git push origin main'",
        "time eval 'git push origin main'",
    ],
)
def test_bash_guard_sees_past_keywords_wrapper_options_and_eval_strings(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert "push" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        "{ git status; }",
        "if git diff --quiet; then git push -u origin HEAD; fi",
        "for f in a b; do echo if then git push origin main; done",
        "env -u X git status",
        "sudo -u x git log --oneline -5",
        "env -S 'git status'",
        "eval 'git status'",
        "eval \"git commit -m 'feat: x'\"",
    ],
)
def test_bash_guard_allows_keywords_wrappers_and_eval_around_allowed_commands(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 0


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        "GIT_CONFIG_GLOBAL=.scratch/config git commit -m 'feat: x'",
        "GIT_CONFIG_SYSTEM=.scratch/config git commit -m 'feat: x'",
        "HOME=.scratch git commit -m 'feat: x'",
        "export HOME=.scratch; git push -u origin feat/x",
        "env XDG_CONFIG_HOME=.scratch git merge origin/feat/x",
        "GIT_DIR=../other/.git git commit -m 'feat: x'",
        "GIT_CONFIG_GLOBAL=.scratch/config bash -c 'git commit -m x'",
        # Other ways to assign them.
        "HOME+=/x git commit -m 'feat: x'",
        "export GIT_CONFIG_GLOBAL+=x; git commit -m 'feat: x'",
        "printf -v HOME %s .scratch; git commit -m 'feat: x'",
        "printf -vGIT_CONFIG_GLOBAL %s .scratch/config; git push -u origin feat/x",
        "read HOME < .scratch/home; git commit -m 'feat: x'",
        "read -r GIT_CONFIG_GLOBAL < .scratch/home; git merge feat/x",
        "source .scratch/env.sh; git commit -m 'feat: x'",
        ". .scratch/env.sh && git push -u origin feat/x",
        ". .scratch/missing.sh && git commit -m 'feat: x'",
    ],
)
def test_bash_guard_denies_config_variables_before_commands_that_run_hooks(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A config file the agent wrote could set core.hooksPath for git's own commit or push."""
    (repo / ".scratch").mkdir()
    (repo / ".scratch" / "env.sh").write_text("export HOME=.scratch\n", encoding="utf-8")
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert "runs hooks" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        "GIT_CONFIG_GLOBAL=.scratch/config git status",
        "printf -v x %s HOME; git commit -m 'feat: x'",
        "read -r line < .scratch/home; git commit -m 'feat: x'",
        "source .scratch/venv.sh && git commit -m 'feat: x'",
        "git commit -F - <<'EOF'\nfix: keep HOME= out of the hook\nEOF",
    ],
)
def test_bash_guard_allows_config_variables_elsewhere(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".scratch").mkdir()
    (repo / ".scratch" / "venv.sh").write_text("PYTHONHOME=x\nPATH=x\n", encoding="utf-8")
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 0


def test_bash_guard_denies_skip_appended_too(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert bash("SKIP+=check git commit -m 'feat: x'", repo, monkeypatch) == 2


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        "git push origin {feat/x,main}",
        "git push origin feat/x{,:main}",
        "git push origin feat/{1..3}",
        "git push origin 'refs/heads/*:refs/heads/*'",
        "git push origin ma?n",
        "git push origin m[a]in",
        "git push {origin,x} feat/x",
        "gh pr create --base main --head {feat/x,y} --title x --body y",
        "gh pr create --base main -H feat/* -t x -b y",
        "gh pr create --base main --head=feat/? -t x -b y",
    ],
)
def test_bash_guard_denies_expansion_in_pushes_and_heads(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """shlex drops quotes, so quoted text is denied too; branch names never hold these."""
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert "brace or glob expansion" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
def test_bash_guard_allows_reflog_braces_in_a_push(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = "git push origin 'HEAD@{1}:refs/heads/feat/x'"
    assert bash(command, repo, monkeypatch) == 0


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize("wrapper", ["bash -c", "eval"])
def test_bash_guard_reads_a_heredoc_inside_a_wrapped_command(
    wrapper: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The heredoc's end comes before the closing quote, so the outer line holds its body."""

    def wrapped(script: str) -> str:
        return f'{wrapper} "{script}\n"'

    checkout(repo, f"feat/{AGENT_TICKET}")
    commit = "git commit -F - <<'EOF'\nfeat: x {id}\nEOF"
    assert bash(wrapped(commit.format(id=AGENT_TICKET)), repo, monkeypatch) == 0
    assert bash(wrapped(commit.format(id=FOREIGN_ID)), repo, monkeypatch) == 2
    assert FOREIGN_ID in capsys.readouterr().err
    assert bash(wrapped("bash <<'EOF'\ngit push origin main\nEOF"), repo, monkeypatch) == 2
    assert "only Zach changes main" in capsys.readouterr().err
    assert bash(wrapped("git commit -F - <<EOF\nfeat: $(date)\nEOF"), repo, monkeypatch) == 2
    assert "shell substitution" in capsys.readouterr().err
    # The outer shell substitutes inside the double quotes, before `<<'EOF'` is read.
    assert bash(wrapped("git commit -F - <<'EOF'\nfeat: $(date)\nEOF"), repo, monkeypatch) == 2
    assert "shell substitution" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        "timeout 120 git push origin main",
        "timeout -k 5 -s KILL 120 git push --force origin feat/x",
        "timeout --signal=KILL --preserve-status 1m git push origin main",
        "timeout -- 120 git push origin main",
        "nice git push origin main",
        "nice -n 5 git push origin main",
        "nice -5 git push origin main",
        "nice --adjustment=5 git push origin main",
        "stdbuf -oL git push origin main",
        "stdbuf -o L -e 0 git push origin main",
        "stdbuf --output=L git push origin main",
        "timeout 60 nice -n 5 env -u X git push origin main",
    ],
)
def test_bash_guard_sees_past_timeout_nice_and_stdbuf(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert "push" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
def test_bash_guard_allows_a_timed_push_of_the_own_branch(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash("timeout 120 git push -u origin HEAD", repo, monkeypatch) == 0
    checkout(repo, "main")
    assert bash("timeout 120 git push -u origin HEAD", repo, monkeypatch) == 2


@pytest.mark.usefixtures("agent_ticket", "other_repo")
@pytest.mark.parametrize(
    "command",
    [
        "env -C ../other git push -u origin HEAD",
        "env --chdir=../other git push",
        "sudo -D ../other git push origin @",
        "sudo --chdir ../other git push -u origin HEAD",
    ],
)
def test_bash_guard_follows_a_wrapper_into_its_directory(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """other_repo is on main; the working repo is on the agent's own branch."""
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert bash(command.replace("../other", "."), repo, monkeypatch) == 0


@pytest.mark.usefixtures("agent_ticket")
def test_bash_guard_reads_message_files_where_env_runs_git(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    message = repo / "sub" / "msg.txt"
    message.parent.mkdir()
    message.write_text(f"feat: x\n\nPart of {AGENT_TICKET}.\n", encoding="utf-8")
    assert bash("env -C sub git commit -F msg.txt", repo, monkeypatch) == 0
    message.write_text(f"feat: x\n\nPart of {FOREIGN_ID}.\n", encoding="utf-8")
    assert bash("env -C sub git commit -F msg.txt", repo, monkeypatch) == 2
    assert FOREIGN_ID in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [
        "env -S 'git\\_push origin main'",
        "env -S 'git push origin ${BRANCH}'",
        "env -S'git push $REMOTE main'",
        "env --split-string='echo \\c'",
    ],
)
def test_bash_guard_denies_env_split_strings_with_escapes(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """env -S has its own escapes (`\\_` is a space) and `${NAME}`, which shlex doesn't read."""
    assert bash(command, repo, monkeypatch) == 2
    assert "env -S" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
def test_bash_guard_checks_alias_expansions_for_bypasses(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / ".git" / "config").write_text(
        "[alias]\n"
        "\tci = commit --no-verify\n"
        "\tsk = -c core.hooksPath=.scratch commit\n"
        "\tsecret = show HEAD:.config/agent-commons/config.json\n",
        encoding="utf-8",
    )
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash("git ci -m 'feat: x'", repo, monkeypatch) == 2
    assert "--no-verify" in capsys.readouterr().err
    assert bash("git sk -m 'feat: x'", repo, monkeypatch) == 2
    assert "core.hooksPath" in capsys.readouterr().err
    assert bash("git secret", repo, monkeypatch) == 2
    assert "credentials" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "fix: deny GIT_DIR= before commit"',
        "git commit -m 'docs: HOME=x and GIT_CONFIG_GLOBAL+=y are denied'",
        "git push -u origin feat/x && git commit -m 'docs: why HOME= is denied'",
    ],
)
def test_bash_guard_allows_config_assignments_as_quoted_text(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 0


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        "HOME=.scratch; git commit -m 'feat: x'",
        "{ HOME=.scratch; git commit -m 'feat: x'; }",
        "declare -x HOME=.scratch; git commit -m 'feat: x'",
        'HOME=".scratch dir" git commit -m x',
        "eval 'export HOME=.scratch'; git commit -m 'feat: x'",
        "eval export HOME=.scratch; git push -u origin feat/x",
    ],
)
def test_bash_guard_still_denies_config_assignments_as_words(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert "runs hooks" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ('env -C "$PWD" git push origin HEAD', "shell expansion"),
        ("env -C ~/x git push", "shell expansion"),
        ("env -C missing git push", "isn't a directory"),
        ("env --chdir=missing git push", "isn't a directory"),
        ("env -C `pwd` git push", "shell expansion"),
        ("env -C $(pwd) git push", "shell expansion"),
        ("sudo -D '{.,..}' git push", "shell expansion"),
        ("sudo --chdir=* git push", "shell expansion"),
        ('git -C "$X" push', "shell expansion"),
        ("git -C ~ push", "shell expansion"),
        ("git -C . -C 'su?' push origin HEAD", "shell expansion"),
    ],
)
def test_bash_guard_denies_directories_it_cant_follow(
    command: str,
    reason: str,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The shell expands these before env, sudo or git see them; the guard would look elsewhere."""
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2
    assert reason in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    ["git push origin HEAD", "git push", "gh pr create --base main --title x --body y"],
)
def test_bash_guard_denies_when_it_cant_read_the_current_branch(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / ".git" / "HEAD").write_text("a1" * 20 + "\n", encoding="utf-8")  # detached
    assert bash(command, repo, monkeypatch) == 2
    assert f"can't read the current branch in {repo}" in capsys.readouterr().err


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        f"make check  # run tests\ngit commit -m 'feat: {FOREIGN_ID}'",
        "echo a#; git push origin main",
        "git status # it's clean\ngit push origin main",
        "echo `true #`; git push origin main",
        "echo ${x:- #}; git push origin main",
        "echo a\\ #; git push origin main",
        "echo $'a\\' # x'; git push origin main",
        'echo "$(date +"%F #")"; git push origin main',
        'echo "$(echo \'a" # b\')"; git push origin main',
        'echo "${x:-$(echo " #")}"; git push origin main',
        'echo "a # b"; git push origin main',
    ],
)
def test_bash_guard_drops_only_real_comments(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `#` starts a comment only where it starts a word outside quotes, as in bash."""
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 2


@pytest.mark.usefixtures("agent_ticket")
@pytest.mark.parametrize(
    "command",
    [
        "echo hi # don't worry",
        "# git push origin main\ngit status",
        "git push -u origin HEAD # don't force it",
        "make check  # run tests\ngit commit -m 'feat: x'",
    ],
)
def test_bash_guard_ignores_comments(
    command: str, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout(repo, f"feat/{AGENT_TICKET}")
    assert bash(command, repo, monkeypatch) == 0


def test_bash_guard_denies_a_nul_byte(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert bash("git commit -m 'feat: x\0'", repo, monkeypatch) == 2


@pytest.mark.parametrize(("stdin", "error"), [("not json", ValueError), ("[]", TypeError)])
def test_bash_guard_crashes_rather_than_allowing(
    stdin: str, error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash never returns 0: python exits 1, which the entry script turns into a block.

    test_settings_wire_each_hook_to_an_entry_script checks that `|| exit 2`.
    """
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    with pytest.raises(error):
        bash_guard.main()


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
