"""Content-addressed storage. Blobs, trees and commits use Git's exact byte format,
so an object here has the same hash that Git gives it."""
import hashlib
import os
import zlib

MODE_FILE = "100644"
MODE_EXEC = "100755"
MODE_TREE = "40000"


class ObjectStore:
    def __init__(self, root):
        self.root = root

    def _path(self, oid):
        return os.path.join(self.root, oid[:2], oid[2:])

    def write(self, kind, body):
        data = f"{kind} {len(body)}".encode() + b"\0" + body
        oid = hashlib.sha1(data).hexdigest()
        path = self._path(oid)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(zlib.compress(data))
            os.replace(tmp, path)
        return oid

    def read(self, oid):
        path = self._path(oid)
        if not os.path.exists(path):
            raise KeyError(f"no such object: {oid}")
        with open(path, "rb") as f:
            data = zlib.decompress(f.read())
        head, _, body = data.partition(b"\0")
        kind, size = head.decode().split()
        if int(size) != len(body):
            raise ValueError(f"object {oid} is corrupt")
        if hashlib.sha1(data).hexdigest() != oid:
            raise ValueError(f"object {oid} does not match its hash")
        return kind, body

    def has(self, oid):
        return os.path.exists(self._path(oid))

    def all(self):
        for d in sorted(os.listdir(self.root)):
            full = os.path.join(self.root, d)
            if len(d) == 2 and os.path.isdir(full):
                for name in sorted(os.listdir(full)):
                    if not name.endswith(".tmp"):
                        yield d + name

    def expand(self, prefix):
        """Find the full id for a short prefix."""
        if len(prefix) == 40:
            return prefix if self.has(prefix) else None
        if len(prefix) < 4:
            return None
        hits = [o for o in self.all() if o.startswith(prefix)]
        return hits[0] if len(hits) == 1 else None


def tree_body(entries):
    """entries: {name: (mode, oid)}. Directories sort as if their name ended in a slash."""
    out = []
    for name in sorted(entries, key=lambda n: n + "/" if entries[n][0] == MODE_TREE else n):
        mode, oid = entries[name]
        out.append(f"{mode} {name}".encode() + b"\0" + bytes.fromhex(oid))
    return b"".join(out)


def parse_tree(body):
    entries, i = {}, 0
    while i < len(body):
        sp = body.index(b" ", i)
        nul = body.index(b"\0", sp)
        mode = body[i:sp].decode()
        name = body[sp + 1:nul].decode()
        entries[name] = (mode, body[nul + 1:nul + 21].hex())
        i = nul + 21
    return entries


def commit_body(tree, parents, author, when, message, zone="+0000"):
    lines = [f"tree {tree}"] + [f"parent {p}" for p in parents]
    lines += [f"author {author} {when} {zone}", f"committer {author} {when} {zone}", "", message]
    text = "\n".join(lines)
    return (text if text.endswith("\n") else text + "\n").encode()


def parse_commit(body):
    head, _, message = body.decode().partition("\n\n")
    info = {"parents": [], "message": message.rstrip("\n")}
    for line in head.split("\n"):
        key, _, value = line.partition(" ")
        if key == "parent":
            info["parents"].append(value)
        elif key == "tree":
            info["tree"] = value
        elif key == "author":
            parts = value.rsplit(" ", 2)
            info["author"], info["when"] = parts[0], int(parts[1])
    return info
