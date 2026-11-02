"""Command line: python -m tinygit <command>"""
import sys
from datetime import datetime, timezone

from .repo import Repo, RepoError, find_root

USAGE = """commands:
  init                     start a repository here
  add PATH...              stage files
  rm PATH                  remove a tracked file
  status                   show what changed
  commit MESSAGE           record the staged files
  log [REV]                show history
  diff [REV [REV]]         show changes
  branch [NAME]            list or create branches
  branch -d NAME           delete a merged branch
  switch REV               move to a branch or commit
  merge REV                merge into the current branch
  tag NAME [REV]           name a commit
  verify                   check every stored object"""


def main(argv):
    args = argv[1:]
    if not args:
        print(USAGE)
        return 2
    cmd, rest = args[0], args[1:]
    try:
        if cmd == "init":
            Repo.init(".")
            print("initialised an empty repository")
            return 0
        r = Repo(find_root())
        if cmd == "add":
            r.add(rest)
        elif cmd == "rm":
            r.rm(rest[0])
        elif cmd == "status":
            s = r.status()
            print("on branch", r.branch_name() or "(detached)")
            for tag, p in s["staged"]:
                print(f"  staged {tag}: {p}")
            for p in s["modified"]:
                print(f"  modified: {p}")
            for p in s["deleted"]:
                print(f"  deleted: {p}")
            for p in s["untracked"]:
                print(f"  untracked: {p}")
        elif cmd == "commit":
            print(r.commit_index(" ".join(rest))[:10])
        elif cmd == "log":
            for c in r.log(rest[0] if rest else "HEAD"):
                when = datetime.fromtimestamp(c["when"], timezone.utc).strftime("%Y-%m-%d %H:%M")
                print(f"{c['id'][:10]}  {when}  {c['message'].splitlines()[0] if c['message'] else ''}")
        elif cmd == "diff":
            print("\n".join(r.diff(*rest)))
        elif cmd == "branch":
            if rest[:1] == ["-d"]:
                r.delete_branch(rest[1])
            elif rest:
                r.create_branch(rest[0])
            else:
                for b in r.branches():
                    print(("* " if b == r.branch_name() else "  ") + b)
        elif cmd == "switch":
            r.switch(rest[0])
        elif cmd == "merge":
            result = r.merge(rest[0])
            print(result)
            return 1 if result == "conflict" else 0
        elif cmd == "tag":
            r.tag(*rest)
        elif cmd == "verify":
            problems = r.verify()
            print("\n".join(problems) or "all objects ok")
            return 1 if problems else 0
        else:
            print(USAGE)
            return 2
    except (RepoError, KeyError, IndexError) as exc:
        print("error:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
