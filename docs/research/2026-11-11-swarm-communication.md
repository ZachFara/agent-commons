# Swarm communication: what prior work says about Commons

Date: 2026-11-11. Ticket: `MIT-683`. Research only; nothing here changes behavior.

The question: has anyone worked out how stateless, non-deterministic agents should
communicate when the only channel is what earlier agents left behind? Yes, under several
names. This note collects the work that maps onto Commons, checks each idea against what
Commons does today, and ends with five ranked proposals the eval harness can test.

Every claim about Commons cites `file:line` on `feature/eval-setup` as of this date. Ledger
lines are line numbers in `docs/eval/trials.jsonl`.

## Short answer

- The closest prior work is **stigmergy**: agents that never talk, and coordinate only through
  traces they leave in a shared environment. Ant colony optimization made this an algorithm,
  and its main lesson is that reinforcement without evaporation or bounds locks the colony
  onto the first decent path.
- **Information cascades** explain our table-extraction run. When each agent sees what earlier
  agents chose and its own evidence is weak, copying is the rational move, and one early choice
  can decide everyone's. A crowd is only wise when its members judge independently.
- **LLM multi-agent work** finds the same thing in models: agents that see each other's answers
  converge, often on a wrong or merely confident one.
- Commons already has more of the defences than most systems: outcomes are observed rather
  than voted, evidence ages, failing entries are reopened, and results are scoped by version.
- What it lacks is any signal for a fix that **works but isn't the right fix**. Tests pass,
  so outcomes reward it, the herding report counts only failures, and the reviewer has no
  prompt to ask. That is the gap our own run shows, and the first two proposals target it.

## Commons in swarm terms

| Swarm idea | Commons today |
|---|---|
| Agent | One Claude Code session: no memory of earlier sessions, a different path each run |
| Trace (pheromone) | An approved entry, plus the outcome rows its retrievals collect |
| Sensing a trace | `search`, which is pull only: the agent has to decide to call it |
| Laying a trace | `propose` (a new entry) and `report` or the Stop hook (outcomes) |
| Reinforcement | The outcome score: a Beta posterior over used outcomes (`server/src/commons/ranking/score.py:29-37`) |
| Evaporation | Outcomes older than 90 days halve in weight every further 90 days (`ranking/score.py:10-11`, `23-25`) |
| Gatekeeper | Human review; trusted contributors go live provisionally and are sampled (`docs/decisions/0028-review-bottleneck.md:21-34`) |

## Prior work

Every link below was opened while writing this note. Where a publisher page was blocked, the
link goes to an author copy or an index page instead.

### Stigmergy and ant colonies

Grassé coined "stigmergy" in 1959 for insects that "work as if they were alone" yet act in a
coordinated way, because each one responds to what the others left behind
([Theraulaz and Bonabeau 1999, *Artificial Life*](https://apice.unibo.it/xwiki/bin/view/Publication/StigmergyhistoryArtificiallife5)).

Ant System turned this into an optimizer. Ants lay pheromone on good paths, later ants prefer
stronger trails, and a factor (1 − ρ) evaporates every trail each round. The authors call
reinforcement "a positive feedback loop" and warn that unchecked it "leads to explosion"
([Dorigo, Maniezzo and Colorni 1996, *IEEE Trans. SMC-B*](https://faculty.washington.edu/paymana/swarm/dorigo96-itsmc.pdf)).

MAX-MIN Ant System fixes the stagnation that follows, "all ants follow the same path and
construct the same solution over and over again". Only the best solutions deposit, every trail
is clamped to [τmin, τmax], and trails start at τmax so early ants explore
([Stützle and Hoos 2000, *Future Generation Computer Systems*](https://www.cs.ubc.ca/~hoos/Publ/fgcs00.pdf)).

**What transfers.** Reinforcement needs both evaporation and a floor, or the first decent
trace wins for good. The floor matters more than the evaporation rate: it guarantees every
option a nonzero chance of being tried.

### Blackboard systems

In Hearsay-II, independent knowledge sources "communicate through a global database called the
blackboard"; any of them can post a hypothesis or modify one
([Erman et al. 1980, *ACM Computing Surveys*](https://websites.nku.edu/~foxr/CSC425/hearsay2.pdf);
[Nii 1986, *AI Magazine*](https://aitopics.org/doc/journals:9007C6C8)).

The design is back for LLM agents. In one, "agents that will take actions are selected based on
the current content of the blackboard"
([Han and Zhang 2025](https://arxiv.org/abs/2507.01701)). In another, a central agent posts
requests and sub-agents volunteer to answer them
([Salemi et al. 2025](https://arxiv.org/abs/2510.01285)). MetaGPT's "shared message pool" lets
agents publish and subscribe by role
([Hong et al. 2023](https://arxiv.org/html/2308.00352v7)).

**What transfers.** Commons is a blackboard without a selector. In the LLM designs above,
something decides which agent acts on what the board holds. Commons can't schedule agents:
each one decides for itself whether to read, and the only lever is the instruction text.

### Gossip and epidemic protocols

Demers et al. kept replicated databases consistent with three mechanisms
([1987, ACM](https://none.cs.umass.edu/~shenoy/courses/fall04/readings/demers.pdf)):

- anti-entropy, where each site regularly reconciles with a random other site: very reliable
  and slow;
- rumor mongering, where a new update is pushed until it keeps reaching sites that already
  have it;
- death certificates, which mark deletions so old copies don't come back. Discarding them
  after a fixed time risks exactly that.

**What transfers.** The push/pull distinction. Commons is pull only, so a trace reaches an
agent only if the agent asks. Tombstones are the other lesson: deletions have to be recorded,
not just performed.

### CRDTs and eventual consistency

Conflict-free replicated data types converge without coordination because their merges are
monotone in a semi-lattice or commute
([Shapiro et al. 2011, SSS](https://www.cs.tufts.edu/~nr/cs257/archive/marc-shapiro/CRDTs_SSS-2011.pdf)).
Eventual consistency promises only that with no new updates, every reader eventually sees the
last value; the gap is the "inconsistency window"
([Vogels 2009, *CACM*](https://web.stanford.edu/class/cs345d-01/rl/eventually-consistent.pdf)).

**What transfers.** Append-only logs of observations merge trivially and stay auditable.
Free-text knowledge does not: two fixes for one symptom neither commute nor have a natural
"max".

### Bandits and exploration

Thompson's 1933 rule is to choose each arm with the probability that it is the best, given the
evidence
([Thompson 1933, *Biometrika*](https://academic.oup.com/biomet/article-lookup/doi/10.1093/biomet/25.3-4.285);
[Russo et al. 2018 tutorial](https://arxiv.org/abs/1707.02038)). UCB1 adds a confidence bonus
to each arm's mean and reaches logarithmic regret
([Auer, Cesa-Bianchi and Fischer 2002, *Machine Learning*](https://homes.di.unimi.it/%7Ecesabian/Pubblicazioni/ml-02.pdf)).

**What transfers.** Greedy ranking on the mean starves arms that have not been tried.
Sampling from the posterior is the cheapest fix when a posterior already exists. Bandits
choose among arms that exist, though: they don't create a missing one.

### Information cascades, herding and the wisdom of crowds

"An informational cascade occurs when it is optimal for an individual, having observed the
actions of those ahead of him, to follow the behavior of the preceding individual without
regard to his own information"
([Bikhchandani, Hirshleifer and Welch 1992, *JPE*](https://snap.stanford.edu/class/cs224w-readings/bikhchandani92fads.pdf)).
Banerjee's sequential model reaches the same place: people do "what others are doing rather
than using their information," and the result is inefficient
([Banerjee 1992, *QJE*](https://ideas.repec.org/a/oup/qjecon/v107y1992i3p797-817..html)).
Cascades form on actions alone and are fragile: new public information can break them.

Crowds are wise under four conditions: diversity, independence, decentralization and
aggregation ([Surowiecki 2004](https://en.wikipedia.org/wiki/The_Wisdom_of_Crowds)). The
experiments show how easily they break:

- "Even mild social influence can undermine the wisdom of crowd effect": seeing others'
  estimates narrowed the group and raised confidence without raising accuracy
  ([Lorenz et al. 2011, *PNAS*](https://pmc.ncbi.nlm.nih.gov/articles/PMC3107299)).
- In an artificial music market, showing earlier downloads "increased both inequality and
  unpredictability of success"
  ([Salganik, Dodds and Watts 2006, *Science*](https://pdodds.w3.uvm.edu/research/papers/years/2006/salganik2006a.pdf)).
- A team of randomly chosen problem solvers beat a team of the best ones, because the best
  lacked diversity ([Hong and Page 2004, *PNAS*](https://pmc.ncbi.nlm.nih.gov/articles/PMC528939/)).

**What transfers.** Show results, not just choices. Showing popularity makes outcomes more
unequal. Diverse partial solutions beat one strong one.

### LLM multi-agent communication and shared memory, 2023-2026

- **Memory streams.** Generative agents keep "a comprehensive record of the agent's
  experience" and retrieve by recency (exponential decay), importance and relevance, equally
  weighted ([Park et al. 2023](https://arxiv.org/abs/2304.03442)).
- **Debate.** Several model instances that debate over rounds improve reasoning and factuality
  ([Du et al. 2023](https://arxiv.org/abs/2305.14325)). But a model that has settled on an
  answer "is unable to generate novel thoughts later", which the authors call
  Degeneration-of-Thought ([Liang et al. 2023](https://arxiv.org/abs/2305.19118)).
- **Debate failures.** Debate does "not reliably outperform" simpler strategies such as
  self-consistency ([Smit et al. 2024, ICML](https://proceedings.mlr.press/v235/smit24a.html)).
  Models "frequently shift from correct to incorrect answers in response to peer reasoning"
  ([Wynn, Satija and Hadfield 2025](https://arxiv.org/abs/2509.05396)). Agents also defer to
  whoever they think is speaking; anonymizing responses reduces it
  ([Choi, Zhu and Li 2025](https://arxiv.org/abs/2510.07517)).
- **Experience libraries.** Voyager keeps "an ever-growing skill library of executable code",
  refined with "environment feedback, execution errors, and self-verification"
  ([Wang et al. 2023](https://arxiv.org/abs/2305.16291)). ExpeL extracts natural-language
  insights from many past tasks ([Zhao et al. 2024, AAAI](https://arxiv.org/abs/2308.10144)).
  Agent Workflow Memory induces "commonly reused routines" from past runs and supplies them
  selectively ([Wang et al. 2024](https://arxiv.org/abs/2409.07429)). Reflexion keeps the
  agent's own critique in an episodic memory
  ([Shinn et al. 2023](https://arxiv.org/abs/2303.11366)).
- **Shared memory across users.** Collaborative Memory splits private from shared memory and
  gives every fragment "immutable provenance attributes"
  ([Rezazadeh et al. 2025](https://arxiv.org/abs/2505.18279)).
- **Recursion.** Models trained on model output lose "tails of the original content
  distribution" ([Shumailov et al. 2024, *Nature*](https://www.pure.ed.ac.uk/ws/portalfiles/portal/460496122/ShumailovEtalNature2024AIModelsCollapseWhen.pdf)).

**What transfers.** Models converge on whatever a confident peer says, so independence has to
be designed in. The libraries that work store verified artifacts, and the best of them
abstract across many runs instead of keeping one run verbatim.

## What our runs show

### Table-extraction: four of six copied the entry's workaround

The task (`gh-table-extraction-bool-form`): FastAPI 0.147 reads an omitted `bool` form field
as False, whatever default it declares (`tools/eval/github_tasks.py:386-389`). The project's
own fix takes the fields as optional strings and parses them, applying the default itself
(`github_tasks.py:399-402`).

The seed session (ledger line 1621) solved it differently. It widened the annotation to
`FormBool = Union[bool, Literal["__unused__"]]`, so FastAPI no longer treats the field as
exactly `bool`, and proposed that as an entry. Measured sessions start from the store as the
seed wave left it, after the owner's review (`docs/eval/README.md:377-382`). Then
`github-same10` ran each arm three times on Sonnet:

| Arm | Ledger lines | Passed | Route taken |
|---|---|---|---|
| `commons` | 1651, 1653, 1654 | 3 of 3 | the entry's `FormBool` union, 3 of 3 |
| `both` (Commons and Context7) | 1643, 1644, 1646 | 3 of 3 | `FormBool` 1; parse strings 2 |
| `context7` | 1647-1649 | 3 of 3 | parse strings 2; a different annotation trick 1 |
| `none` (blind) | 1655-1657 | 3 of 3 | parse strings, 3 of 3 |

All six Commons sessions retrieved the same entry at rank 1. Used detection got it right:
`used` is `yes` on exactly the four `FormBool` sessions, `partial` on 1644 and `no` on 1646.

This is a cascade in the textbook sense. The three blind sessions, working independently, all
found the upstream route. Four of the six sessions that could see the entry took the entry's
route instead. Nothing failed, so nothing in Commons noticed:

- The outcome score rewards it: each copy is a hook-observed `tests_passed`
  (`server/src/commons/store/ranking.py:9-19`).
- The eval's herding report counts only failed sessions sharing a wrong patch
  (`tools/agent_eval.py:987-998`, `1088-1100`), so this run scores zero herding.
- The review card shows cause, fix, repro, environment, domains, redactions, risks and
  likely duplicates (`server/src/commons/web/templates/queue.html:11-29`). Nothing asks
  whether the fix is the library's own route or a workaround.
- FastAPI 0.162.0 went back to the declared default (`github_tasks.py:388-389`). The version
  factor demotes only a different major version, and gives any other minor of the same major
  1.0 (`server/src/commons/domain/search.py:209-218`). FastAPI is on major 0, so a caller on
  0.162 sees a 0.147 entry at full weight unless an admin retires it with `fixed_in`
  (`docs/decisions/0026-entry-lifecycle.md:31-33`).

The decision record for these runs predicted the general case: "A bad entry approved in the
review can mislead every Commons session at once"
(`docs/decisions/2026-11-07-MIT-565-github-task-arms.md:190-191`). This entry wasn't bad. It
was a working workaround, which is harder to catch.

### Pip-tools: zero of six searched

On `gh-pip-tools-flag-default`, none of the six Commons sessions (`both` at lines 1627-1629,
`commons` at 1635, 1636, 1638) called `search`, and all six passed after one or two earlier
test runs (`attempts`, "test runs before the last one", `tools/eval/runner.py:195`). Only the seed session (line 1619) searched. The shipped
rule is "before your first fix for a failure that involves a dependency or the runtime, call
`search`" (`server/src/commons/service/agent_text.py:267-268`, shipped as `SEARCH_RULES[4]`,
`agent_text.py:530`).

In stigmergy terms: a trace only acts on agents that sense it, and in Commons sensing is the
agent's choice. Earlier interviews give the agents' own reasons for skipping: "the agent read
library source instead of searching" (9 of 26) and "search was seen as help when stuck rather
than a duplicate check" (6 of 26) (`docs/eval/README.md:764-770`). Here no one was harmed: the
task was easy. The eval already answers this for measurement with a difficulty screen that
keeps only tasks blind Sonnet finds hard (`docs/eval/README.md:187-204`).

### Older ledger evidence

- Agents laying a new trail beside an existing one: Opus "re-proposing fixes the store already
  held in 10 of 12 known-entry sessions" under one wording (`docs/eval/README.md:750-754`).
  The search-before-propose check fixed it in later rounds (`README.md:760-762`). Duplicate
  detection catches the rest at review (`docs/decisions/0027-duplicate-resolution.md:31-34`).
- Untrusted traces: in the injection scenario, no session in any condition followed the
  planted instructions (`docs/eval/README.md:737-741`).

## Each idea against Commons

| Idea | Commons does | Commons doesn't | Evidence |
|---|---|---|---|
| Evaporation (ACO) | Outcome weight halves every 90 days after the first 90 (`ranking/score.py:10-11`, `23-25`), so the score drifts back to the prior | An entry with no outcomes scores 0.5 forever (`store/search.py:38`). Approval never fades. Nothing reacts to the library changing. Decay flags in `status` are not built (`domain/status.py:23`) | FastAPI 0.162 reverted the behavior; the 0.147 entry keeps full weight |
| Bounds on reinforcement (MAX-MIN) | A prior worth two outcomes keeps new entries off 0 and 1 (`ranking/score.py:7-8`) | No floor on the chance of being shown: below the relevance cutoff (0.25) or past `limit` (5), an entry earns nothing (`store/search.py:131-136`, `domain/search.py:140`, `mcp/server.py:110`) | Not tested: every symptom in our runs had one entry |
| Blackboard | One shared store all agents read and write | No control component: nothing schedules which agent looks at what. The only lever is the instruction text | Pip-tools: 0 of 6 looked |
| Gossip (push) | Nightly `commons rank` is a batch anti-entropy pass (decision 0017, `docs/decisions/0017-ranking-v0.md:19-22`) | Hooks push no entries. Both nudges are about proposing, not searching (`service/agent_text.py:131-136`, `158-162`; `service/hooks.py:151-165`) | Pip-tools |
| Tombstones, CRDT-style append-only state | Raw observations are never deleted; corrections record old and new values (`DESIGN.md:107-110`). Retirement is a status change, never a delete (`0026-entry-lifecycle.md:34`) | Merging entries is a human act, not automatic (`0027-duplicate-resolution.md:31-34`) | Nothing against it |
| Bandits (Thompson, UCB) | Ranks greedily by the posterior mean (`store/search.py:135`) | No exploration. A Thompson draw is designed but Proposed, behind a trigger (`docs/decisions/2026-11-07-MIT-582-search-exploration.md:34-62`) | Exploration picks among entries; table-extraction had one |
| Cascades | Shows outcomes, not just choices: the provenance line gives `success S/N when used` (`domain/search.py:106-111`) | Tells agents to follow the crowd: "prefer entries with successes" (`service/agent_text.py:58-62`). A pass can't tell the right fix from a working workaround | Table-extraction, 4 of 6 |
| Wisdom of crowds (independence, diversity) | "Keep both" records a different fix for the same symptom (`0027-duplicate-resolution.md:34`) | Search doesn't put alternatives side by side; agents see the top entry first | Blind arm: 3 of 3 independent sessions found the upstream route |
| LLM convergence on a confident peer | Tells agents to "test any fix you apply" (`service/agent_text.py:32-33`); hooks observe the test | Shows a confident cause and fix, the format that moves models most (`2026-11-07-MIT-582-evidence-counts-in-search.md:14-19`) | Test oracle catches wrong fixes, not workarounds (same record, `21-24`) |
| Experience libraries (Voyager, ExpeL, AWM) | Stores one verified fix per discovery; outcomes verify it afterwards | No abstraction across sessions: an entry is one session's fix, verbatim. Failures live only as counts | Table-extraction: one session's workaround became everyone's |

## Proposals, ranked

Ranked by what our evidence supports and what each costs. The first is measurement only and
the others depend on it.

### 1. See herds that pass

- **What.** Give each GitHub task an optional `upstream_route`: one concrete span from the
  upstream fix (for table-extraction, `Optional[str] = Form(None)`). `compare` reports, per arm
  and task, the share of passing sessions whose patch contains it, next to the share that used
  a retrieved entry. Herding then has two lines: failed sessions sharing a wrong patch (today)
  and passing sessions that left the upstream route for an entry's.
- **Cost.** Small. `tools/eval/github_tasks.py`, `tools/agent_eval.py` and their tests. It
  replays the ledger and runs no session.
- **Risk.** The span is hand-picked per task, and some tasks have several good routes; leave it
  unset there. `patch_excerpt` is scrubbed and can be cut, so a long patch may hide the span.
- **Test.** Replay `github-same10`: it should show table-extraction at `none` 3 of 3 and
  `commons` 0 of 3 on the upstream route. **Kill it** if, on the full `github-arms` run, the
  Commons arms' share on the upstream route is no lower than `none`'s on every task where an
  entry was used: then herding is rare and proposals 2 and 4 aren't needed.

### 2. Ask "upstream route or workaround?" at review

- **What.** The review card asks the reviewer whether the fix is the library's own route (its
  changelog, its migration notes, the project's upstream fix) or a workaround, and records the
  answer. Search provenance adds "workaround" to the line of entries marked so. The same
  question could be one short clause in `propose`'s description, so agents say it themselves.
- **Cost.** Small to medium: a template, a stored answer (a review field, which is a
  migration), one provenance word. A change to what agents see is a new prompt version and
  needs an eval (decision 0023).
- **Risk.** Review is already the bottleneck (decision 0028), and this adds a judgment that
  takes looking up the library. Agents without web tools can't know the upstream route. Calling
  an entry a workaround may make agents skip a fix that is the only one there is.
- **Test.** A new condition on the table-extraction store with the entry marked as a
  workaround, against `github-same10`. **Kill it** if the Commons arms' upstream-route share
  (proposal 1) doesn't rise, or the pass rate falls.

### 3. Evaporate on version change, not only with time

- **What.** Treat each minor of a 0.x library as its own major in the version factor, as
  semver does for 0.x, so a 0.147 entry is demoted for a 0.162 caller. Later, keep it shown but
  marked when the caller is past every version the entry has a recorded success on.
- **Cost.** Small: `domain/search.py:196-218` and its tests.
- **Risk.** Many 0.x libraries keep behavior across minors; this demotes their entries for
  everyone on a newer minor. It needs callers to pass `environment.libraries`, which the
  `search` tool's description doesn't mention (`AGENTS.md:213`).
- **Test.** The search-relevance gate of decision 0025: `make search-eval` on the judged set.
  **Kill it** if precision@5 or recall@5 falls, the rule that gate already applies.
- **Why not faster time decay.** Ant colonies tune evaporation for many ants per iteration.
  Commons sees a few sessions a day; decaying faster than 90 days would erase evidence before
  anyone could renew it. Version change is the event that actually makes a trace stale.

### 4. Show an alternative beside the top entry

- **What.** When the top hit has a sibling the reviewer kept distinct ("keep both", decision
  0027) or another entry with the same symptom and a different fix, return it right after,
  labelled as another fix for this symptom. This is the diversity condition from the
  wisdom-of-crowds work, applied to one result list.
- **Cost.** Medium: a store query over `distinct_pairs`, a label in the hit, a prompt version,
  and an eval condition with two seeded entries.
- **Risk.** More tokens per search. Agents may still take the first. It only works once the
  store holds alternatives, which needs proposal 2 (or a reviewer keeping both).
- **Test.** A scenario seeded with two approved entries for one symptom, a workaround and the
  upstream route, with equal outcome history; condition `alternatives` against the baseline.
  **Kill it** if the upstream-route share (proposal 1) doesn't rise, or tokens per passing
  session rise by more than 20%, the margin the arms record uses (`tools/agent_eval.py:810`).

### 5. "N used, M failed", measured for herding too

- **What.** The Proposed evidence-counts record: replace "success S/N when used" with "used 7
  times: 3 worked, 2 failed, 2 no result", by model
  (`2026-11-07-MIT-582-evidence-counts-in-search.md:29-38`). It is already designed with its
  own eval (`known-wrong-entry`).
- **Cost.** As that record says: no schema change; `seed()` must write outcome history first.
- **Risk.** It helps only when failures exist. On table-extraction every copy passed, so the
  line would read "3 worked, 0 failed" and pull harder toward the workaround. The cultural
  market experiment found that showing popularity makes outcomes more unequal and less
  predictable.
- **Test.** The record's adoption rule, plus proposal 1's measure on the GitHub tasks. **Kill
  it** if the upstream-route share falls against the shipped line.

### Thompson sampling: keep the trigger

Commons has no fixed exploration to replace: it ranks by the mean. The exploration record
already chose a Thompson draw over an epsilon slot and UCB, because it uses the posterior
ranking already computes and adds no setting
(`2026-11-07-MIT-582-search-exploration.md:45-62`, `128-134`). Our runs give no reason to build
it sooner. Exploration picks among existing entries, and the herd formed around a symptom with
only one. The trigger on the Searches page (`search-exploration.md:34-44`) is the right gate.

## What not to borrow

- **Reinforcement by use alone.** Ants deposit pheromone for walking a path. Commons must
  never rank by retrieval or use counts, only by observed outcomes, as it does now
  (`ranking/score.py:15-26`). Popularity is the cascade's fuel.
- **Debate or voting between agents.** Consensus among models converges, and often on the
  wrong answer. Commons has something better: a test the hooks observe.
- **Gossip-style pushing of entries into every session.** Cheap for databases, expensive for
  agents: every pushed entry costs context and invites copying. Pip-tools passed without
  searching.
- **Automatic merging, CRDT-style.** Two fixes for one symptom don't commute. Keeping both,
  or a human merge, is the right semantics (decision 0027). The append-only outcome log is the
  part worth keeping, and Commons already has it.
- **Hiding outcomes to stop cascades.** Cascades form when people see choices without
  results. Showing results is the cure; Commons should show more of them, not fewer.
- **Self-assessment as the reward.** Reflexion-style self-critique is useful inside one
  session. As a ranking input it repeats the self-report problem DESIGN.md §7 excludes
  (`DESIGN.md:215`).
- **Agents learning only from agents.** Models trained on their own output lose the tails of
  the distribution. A store written and judged only by agents could drift the same way; human
  review and observed tests are what anchor it.
