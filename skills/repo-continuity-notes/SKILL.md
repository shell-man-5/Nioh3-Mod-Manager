---
name: repo-continuity-notes
description: Repo-local continuity workflow for this codebase. Use when starting work in this repo and the relevant context is not already active, even for small changes. Also use for reviews, architecture changes, or any approved code change that should update the durable memo set. Read the memo index first, follow the relevant topic memos, summarize the applicable constraints, and update the touched memos after the change.
---

# Repo Continuity Notes

This repo keeps continuity as topic memos, not one giant document.

## Workflow

1. If the relevant repo context is not already active in working memory, read `docs/memos/index.md` first.
2. Use the task lookup there to choose the smallest relevant memo set.
3. Read only those topic memos unless a cross-link says another memo matters.
4. Summarize the relevant constraints before changing code.
5. After approved changes that materially affect behavior, architecture, workflow, tests, or important gotchas, update the touched memo(s).
6. If the topic map changed, update the index too.
7. If the change is tiny and purely local, do not force a memo edit unless it improves future recovery.

## What The Memos Are For

- Keep feature and architecture context durable across sessions.
- Keep review scope small.
- Prevent re-discovery of the same backend, archive, UI, or migration rules.

## What To Update

- Add durable behavior changes.
- Add routing/state assumptions.
- Add gotchas that are easy to regress.
- Add new entry points or file ownership changes.
- Add new sandbox/manual QA flows if they become part of the normal workflow.
- Add small-but-important clarifications when the change itself is small but the recovered context would otherwise be weak.

## What Not To Update

- Do not paste diffs.
- Do not log every small edit.
- Do not duplicate the same note across many files unless a short cross-link is better than forcing future sessions to guess.
