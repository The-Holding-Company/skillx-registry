---
id: a052b7b9-90e4-46cb-960e-30bba01673df
slug: skillx-customer-skill-submission
entity: holdingco
---

# skillx-registry — the public registry + transparency log for skillx.md

This is the canonical, public home of the [skillx.md](https://skillx.md/)
marketplace registry (`registry/skills.json`) and its append-only
transparency log (`log/transparency.jsonl`). Skills are **never hosted
here** — they live on the publisher's own domain, which is the trust root.
This repo only records *what was verified, when, and under which key*.

## Submit a skill (one URL)

1. Sign and host your skill on your domain — 5-minute guide:
   <https://skillx.md/publish.html>
2. Open a [**Submit a skill** issue](../../issues/new?template=submit-skill.yml)
   with the URL of your signed `SKILL.md`.
3. Automation re-verifies the signature against your live URL, runs a content
   scan, and opens a registry PR on your behalf. A maintainer review is the
   final gate; **merge = published** — the entry appears at
   [skillx.md/skills.html](https://skillx.md/skills.html).

Prefer raw git? Open the PR yourself: add one entry to
`registry/skills.json` and append one line to `log/transparency.jsonl`
(fields per [SPEC §5](https://skillx.md/spec.md)). CI re-verifies every
entry either way.

## Invariants

- **The log is append-only.** Existing lines are never edited or removed —
  enforced in CI, not by trust. Listings that later fail re-verification are
  pulled from the registry and *annotated* in the log, never silently deleted.
- **A signature proves who published, never that content is safe.** Every
  submission is content-scanned in addition to signature-verified.
- Signing, verifying, and listing are free, forever. Verified Publisher
  (identity-checked badge, $9/yr founding): <https://skillx.md/#pricing>.

## Layout

```
registry/skills.json       Marketplace index (synced to skillx.md/skills.json)
log/transparency.jsonl     Append-only log, one JSON line per signature event
skillx.py                  Vendored CLI (canonical copy served at skillx.md/skillx.py)
scripts/verify_registry.py CI gate: every entry must verify against its live URL
scripts/add_entry.py       Turns a verified skill URL into a registry entry + log line
```

## Notes for maintainers

- Bot-opened PRs (from the submission workflow) don't retrigger
  `pull_request` CI because they're pushed with `GITHUB_TOKEN`; the submit
  workflow runs the full `verify_registry.py` pass itself and attaches the
  output to the PR. `workflow_dispatch` re-runs it on demand.
- Merged registry/log changes go live automatically: the
  `sync-skillx-registry` workflow in `landing-pages` pulls this repo's
  `main` every 30 minutes and deploys on change (`workflow_dispatch` it for
  an instant sync). ([HC-960](https://linear.app/holdingco11/issue/HC-960))
- The skillx.md publish-page form feeds the same pipeline: form →
  webhook-nats-bridge → `skillx-submit-worker` (verify + content scan) →
  a `skill-submission` issue here → the normal automation.

Part of the User Agency Web (username.md · about-me.md · finger.md ·
about-us.md · badge.md). CLI + spec source: `The-Holding-Company/skillx`.
