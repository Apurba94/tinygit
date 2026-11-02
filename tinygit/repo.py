"""A repository: index, refs, commits, branches and merges."""
import json
import os
import shutil
import time

from .diff import merge3, unified
from .objects import MODE_EXEC, MODE_FILE, MODE_TREE, ObjectStore, commit_body, parse_commit, parse_tree, tree_body

DIR = ".tinygit"


class RepoError(Exception):
    pass


def find_root(start="."):
    p = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(p, DIR)):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            raise RepoError("not inside a TinyGit repository")
        p = parent


class Repo:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.git = os.path.join(self.root, DIR)
        self.store = ObjectStore(os.path.join(self.git, "objects"))
        self.author = os.environ.get("TINYGIT_AUTHOR", "Anonymous <anon@example.org>")
        self.clock = lambda: int(time.time())

    @classmethod
    def init(cls, root):
        os.makedirs(root, exist_ok=True)
        r = cls(root)
        if os.path.exists(r.git):
            raise RepoError("already a repository")
        os.makedirs(os.path.join(r.git, "objects"))
        os.makedirs(os.path.join(r.git, "refs", "heads"))
        os.makedirs(os.path.join(r.git, "refs", "tags"))
        r._write("HEAD", "ref: refs/heads/main\n")
        r._save_index({})
        return r

    def _read(self, rel):
        path = os.path.join(self.git, rel)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def _write(self, rel, text):
        path = os.path.join(self.git, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)

    def _index(self):
        return json.loads(self._read("index") or "{}")

    def _save_index(self, index):
        self._write("index", json.dumps(index, sort_keys=True, indent=0))

    # refs

    def branch_name(self):
        head = self._read("HEAD").strip()
        return head[len("ref: refs/heads/"):] if head.startswith("ref: ") else None

    def head(self):
        head = self._read("HEAD").strip()
        if head.startswith("ref: "):
            value = self._read(head[5:])
            return value.strip() if value else None
        return head

    def _set_head(self, oid):
        name = self.branch_name()
        if name:
            self._write(f"refs/heads/{name}", oid + "\n")
        else:
            self._write("HEAD", oid + "\n")

    def resolve(self, rev):
        """A branch, tag, HEAD, a commit id or prefix, with optional ~n suffix."""
        base, _, steps = rev.partition("~")
        if base == "HEAD":
            oid = self.head()
        else:
            oid = None
            for kind in ("heads", "tags"):
                value = self._read(f"refs/{kind}/{base}")
                if value:
                    oid = value.strip()
                    break
            if oid is None:
                oid = self.store.expand(base)
        if oid is None:
            raise RepoError(f"unknown revision: {rev}")
        for _ in range(int(steps or 0) if "~" in rev else 0):
            parents = self.commit(oid)["parents"]
            if not parents:
                raise RepoError(f"{rev} goes past the first commit")
            oid = parents[0]
        return oid

    def commit(self, oid):
        kind, body = self.store.read(oid)
        if kind != "commit":
            raise RepoError(f"{oid} is not a commit")
        info = parse_commit(body)
        info["id"] = oid
        return info

    # trees

    def _flat(self, tree, prefix=""):
        out = {}
        for name, (mode, oid) in parse_tree(self.store.read(tree)[1]).items():
            path = prefix + name
            if mode == MODE_TREE:
                out.update(self._flat(oid, path + "/"))
            else:
                out[path] = [mode, oid]
        return out

    def tree_files(self, commit):
        return self._flat(self.commit(commit)["tree"]) if commit else {}

    def _write_tree(self, files):
        """Build nested tree objects from {path: [mode, oid]} and return the root tree id."""
        def build(items):
            entries, subs = {}, {}
            for path, value in items.items():
                head, _, rest = path.partition("/")
                if rest:
                    subs.setdefault(head, {})[rest] = value
                else:
                    entries[head] = tuple(value)
            for name, sub in subs.items():
                entries[name] = (MODE_TREE, build(sub))
            return self.store.write("tree", tree_body(entries))
        return build(files)

    # working directory

    def walk(self):
        ignore = {DIR}
        pats = []
        path = os.path.join(self.root, ".tinygitignore")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                pats = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        import fnmatch
        for base, dirs, files in os.walk(self.root):
            rel_base = os.path.relpath(base, self.root).replace("\\", "/")
            rel_base = "" if rel_base == "." else rel_base + "/"
            dirs[:] = sorted(d for d in dirs if d not in ignore and not any(fnmatch.fnmatch(d, p.rstrip("/")) for p in pats))
            for f in sorted(files):
                if not any(fnmatch.fnmatch(f, p) or fnmatch.fnmatch(rel_base + f, p) for p in pats):
                    yield rel_base + f

    def _hash_file(self, rel):
        full = os.path.join(self.root, rel)
        with open(full, "rb") as f:
            oid = self.store.write("blob", f.read())
        return [MODE_EXEC if os.access(full, os.X_OK) and os.name != "nt" else MODE_FILE, oid]

    def add(self, paths):
        index = self._index()
        known = set(self.walk())
        for p in paths:
            rel = os.path.relpath(os.path.join(self.root, p), self.root).replace("\\", "/")
            if rel == ".":
                rel = ""
            hits = [k for k in known if k == rel or not rel or k.startswith(rel + "/")]
            if hits:
                for h in hits:
                    index[h] = self._hash_file(h)
            removed = [k for k in index if (k == rel or not rel or k.startswith(rel + "/")) and k not in known]
            for k in removed:
                del index[k]
            if not hits and not removed:
                raise RepoError(f"no such file: {p}")
        self._save_index(index)

    def rm(self, path):
        index = self._index()
        if path not in index:
            raise RepoError(f"not tracked: {path}")
        del index[path]
        self._save_index(index)
        full = os.path.join(self.root, path)
        if os.path.exists(full):
            os.remove(full)

    def status(self):
        index = self._index()
        head = self.tree_files(self.head())
        result = {"staged": [], "modified": [], "deleted": [], "untracked": []}
        for p in sorted(set(index) | set(head)):
            if index.get(p) != head.get(p):
                tag = "new" if p not in head else "deleted" if p not in index else "modified"
                result["staged"].append((tag, p))
        files = set(self.walk())
        for p, value in index.items():
            if p not in files:
                result["deleted"].append(p)
            elif self._peek(p) != value[1]:
                result["modified"].append(p)
        result["untracked"] = sorted(files - set(index))
        result["modified"].sort()
        result["deleted"].sort()
        return result

    def _peek(self, rel):
        import hashlib
        with open(os.path.join(self.root, rel), "rb") as f:
            body = f.read()
        return hashlib.sha1(f"blob {len(body)}".encode() + b"\0" + body).hexdigest()

    def commit_index(self, message, extra_parent=None):
        if self._read("MERGE_HEAD") and extra_parent is None:
            extra_parent = self._read("MERGE_HEAD").strip()
            if any(self._has_conflict(p) for p in self._read("CONFLICTS").split("\n") if p):
                raise RepoError("resolve the conflicts and add the files first")
        index = self._index()
        head = self.head()
        if not index and not head:
            raise RepoError("nothing to commit")
        tree = self._write_tree(index)
        if head and self.commit(head)["tree"] == tree and not extra_parent:
            raise RepoError("nothing to commit")
        parents = ([head] if head else []) + ([extra_parent] if extra_parent else [])
        oid = self.store.write("commit", commit_body(tree, parents, self.author, self.clock(), message))
        self._set_head(oid)
        for name in ("MERGE_HEAD", "CONFLICTS"):
            path = os.path.join(self.git, name)
            if os.path.exists(path):
                os.remove(path)
        return oid

    def _has_conflict(self, path):
        full = os.path.join(self.root, path)
        if not os.path.exists(full):
            return False
        with open(full, encoding="utf-8", errors="replace") as f:
            return any(l.startswith("<<<<<<< ") for l in f)

    def log(self, rev="HEAD", limit=None):
        """Commits reachable from rev, newest first."""
        try:
            start = self.resolve(rev)
        except RepoError:
            return []
        seen, todo, out = set(), [start], []
        while todo and (limit is None or len(out) < limit):
            todo.sort(key=lambda o: self.commit(o)["when"], reverse=True)
            oid = todo.pop(0)
            if oid in seen:
                continue
            seen.add(oid)
            info = self.commit(oid)
            out.append(info)
            todo.extend(info["parents"])
        return out

    # branches

    def branches(self):
        folder = os.path.join(self.git, "refs", "heads")
        out = []
        for base, _, files in os.walk(folder):
            for f in files:
                out.append(os.path.relpath(os.path.join(base, f), folder).replace("\\", "/"))
        return sorted(out)

    def create_branch(self, name, rev="HEAD"):
        if self._read(f"refs/heads/{name}"):
            raise RepoError(f"branch {name} already exists")
        self._write(f"refs/heads/{name}", self.resolve(rev) + "\n")

    def delete_branch(self, name):
        if name == self.branch_name():
            raise RepoError("cannot delete the current branch")
        path = os.path.join(self.git, "refs", "heads", name)
        if not os.path.exists(path):
            raise RepoError(f"no such branch: {name}")
        tip = self._read(f"refs/heads/{name}").strip()
        if self.head() and self.base(self.head(), tip) != tip:
            raise RepoError(f"{name} has work not merged here")
        os.remove(path)

    def tag(self, name, rev="HEAD"):
        self._write(f"refs/tags/{name}", self.resolve(rev) + "\n")

    def _dirty(self):
        s = self.status()
        return s["staged"] or s["modified"] or s["deleted"]

    def _checkout_tree(self, target):
        """Replace the tracked working files and the index with those of a commit."""
        old = self._index()
        new = self.tree_files(target)
        for p in old:
            if p not in new:
                full = os.path.join(self.root, p)
                if os.path.exists(full):
                    os.remove(full)
                self._prune(os.path.dirname(full))
        for p, (mode, oid) in new.items():
            if old.get(p) != [mode, oid]:
                self._write_file(p, self.store.read(oid)[1])
        self._save_index(new)

    def _prune(self, folder):
        while os.path.abspath(folder) != self.root and os.path.isdir(folder) and not os.listdir(folder):
            shutil.rmtree(folder)
            folder = os.path.dirname(folder)

    def _write_file(self, rel, data):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)

    def switch(self, rev):
        if self._dirty():
            raise RepoError("you have uncommitted changes: commit them first")
        is_branch = self._read(f"refs/heads/{rev}") is not None
        oid = self.resolve(rev)
        untracked = set(self.status()["untracked"])
        clash = untracked & set(self.tree_files(oid))
        if clash:
            raise RepoError(f"untracked files would be overwritten: {', '.join(sorted(clash))}")
        self._checkout_tree(oid)
        self._write("HEAD", f"ref: refs/heads/{rev}\n" if is_branch else oid + "\n")

    def base(self, a, b):
        """The merge base: the most recent commit reachable from both."""
        ancestors = set()
        todo = [a]
        while todo:
            o = todo.pop()
            if o not in ancestors:
                ancestors.add(o)
                todo.extend(self.commit(o)["parents"])
        best = None
        seen, todo = set(), [b]
        while todo:
            o = todo.pop()
            if o in seen:
                continue
            seen.add(o)
            if o in ancestors:
                info = self.commit(o)
                if best is None or info["when"] > self.commit(best)["when"]:
                    best = o
                continue
            todo.extend(self.commit(o)["parents"])
        return best

    def merge(self, rev):
        """Merge a branch into the current one. Returns 'up-to-date', 'fast-forward', 'merged' or 'conflict'."""
        if self._dirty():
            raise RepoError("you have uncommitted changes: commit them first")
        ours, theirs = self.head(), self.resolve(rev)
        base = self.base(ours, theirs)
        if base == theirs:
            return "up-to-date"
        if base == ours:
            self._checkout_tree(theirs)
            self._set_head(theirs)
            return "fast-forward"
        a, b, o = self.tree_files(ours), self.tree_files(theirs), self.tree_files(base)
        result, conflicts = {}, []
        for p in sorted(set(a) | set(b) | set(o)):
            ea, eb, eo = a.get(p), b.get(p), o.get(p)
            if ea == eb:
                if ea:
                    result[p] = ea
            elif ea == eo:
                if eb:
                    result[p] = eb
            elif eb == eo:
                if ea:
                    result[p] = ea
            elif ea and eb:
                merged = self._merge_file(p, ea, eb, eo, rev)
                result[p] = [ea[0], self.store.write("blob", merged[0])]
                if merged[1]:
                    conflicts.append(p)
            else:
                keep = ea or eb
                result[p] = keep
                conflicts.append(p)
        self._apply_merge(result, conflicts)
        if conflicts:
            self._write("MERGE_HEAD", theirs + "\n")
            self._write("CONFLICTS", "\n".join(conflicts))
            return "conflict"
        self.commit_index(f"Merge {rev}", extra_parent=theirs)
        return "merged"

    def _merge_file(self, path, ea, eb, eo, rev):
        def text(e):
            return self.store.read(e[1])[1].decode("utf-8", "replace").split("\n") if e else []
        lines, count = merge3(text(eo), text(ea), text(eb), "HEAD", rev)
        return "\n".join(lines).encode(), count

    def _apply_merge(self, files, conflicts):
        old = self._index()
        for p in old:
            if p not in files:
                full = os.path.join(self.root, p)
                if os.path.exists(full):
                    os.remove(full)
                self._prune(os.path.dirname(full))
        for p, (mode, oid) in files.items():
            if old.get(p) != [mode, oid]:
                self._write_file(p, self.store.read(oid)[1])
        self._save_index(files)

    def diff(self, a=None, b=None):
        """Patch between two revisions, or between HEAD and the working files."""
        left = self.tree_files(self.resolve(a) if a else self.head())
        if b:
            right = {p: self.store.read(v[1])[1] for p, v in self.tree_files(self.resolve(b)).items()}
        else:
            right = {}
            for p in self._index():
                full = os.path.join(self.root, p)
                if os.path.exists(full):
                    with open(full, "rb") as f:
                        right[p] = f.read()
        lines = []
        for p in sorted(set(left) | set(right)):
            before = self.store.read(left[p][1])[1] if p in left else b""
            after = right.get(p, b"")
            if before != after:
                lines.extend(unified(before.decode("utf-8", "replace").splitlines(), after.decode("utf-8", "replace").splitlines(), "a/" + p, "b/" + p))
        return lines

    def verify(self):
        """Re-hash every object and check that each commit's tree exists. Returns problems."""
        problems = []
        for oid in self.store.all():
            try:
                self.store.read(oid)
            except ValueError as exc:
                problems.append(str(exc))
        return problems
