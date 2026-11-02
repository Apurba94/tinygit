# TinyGit

Version control built from scratch in pure Python. Blobs, trees and commits use Git's exact byte format, so the same content gets the same hash in both tools.

```
python -m tinygit init
python -m tinygit add .
python -m tinygit commit "First version"
python -m tinygit branch idea
python -m tinygit switch idea
python -m tinygit merge main
python -m tinygit log
```

Set your name once with the `TINYGIT_AUTHOR` environment variable, for example `Rahima <rahima@example.org>`.

## What is inside

| Part | File | How it works |
|---|---|---|
| Object store | `objects.py` | Files, folders and commits are stored by the SHA-1 of their content, compressed, and re-checked on every read |
| Diff | `diff.py` | Myers' shortest-edit algorithm, with standard unified patches |
| Merge | `diff.py`, `repo.py` | Finds the common ancestor, then merges line by line and writes conflict markers where both sides changed the same lines |
| Repository | `repo.py` | Index, refs, branches, tags, detached HEAD and safety checks that stop you overwriting uncommitted or untracked work |

## How it is tested

- Random diffs are checked against an independent longest-common-subsequence count, to prove the edit scripts are the shortest possible.
- Random patches are applied to prove they rebuild the target.
- When Git is installed, a test asks real Git for the tree and commit ids of the same content and requires an exact match.
- Deliberately damaged objects must be caught by `verify`.

## Limits

TinyGit has no remotes, packfiles, rebase or submodules, and it stores loose objects only. It keeps its data in `.tinygit`, so a folder can be tracked by Git and TinyGit together.

MIT licence.
