import os
import random
import shutil
import subprocess
import tempfile
import unittest

from tinygit import Repo, RepoError, apply_patch, merge3, myers, unified
from tinygit.objects import commit_body, parse_commit, parse_tree, tree_body

HAS_GIT = shutil.which("git") is not None


def lcs(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


class TestDiff(unittest.TestCase):
    def test_simple(self):
        s = myers(list("abc"), list("axc"))
        self.assertEqual("".join(o for o, _ in s).count(" "), 2)

    def test_random_scripts_are_correct_and_shortest(self):
        rng = random.Random(1)
        for _ in range(300):
            a = [rng.choice("abcd") for _ in range(rng.randint(0, 30))]
            b = [rng.choice("abcd") for _ in range(rng.randint(0, 30))]
            s = myers(a, b)
            self.assertEqual([l for o, l in s if o != "+"], a)
            self.assertEqual([l for o, l in s if o != "-"], b)
            self.assertEqual(sum(1 for o, _ in s if o == " "), lcs(a, b))

    def test_patches_apply(self):
        rng = random.Random(2)
        for _ in range(200):
            a = [str(rng.randint(0, 9)) for _ in range(rng.randint(0, 60))]
            b = list(a)
            for _ in range(rng.randint(0, 8)):
                op = rng.randrange(3)
                i = rng.randint(0, len(b))
                if op == 0:
                    b.insert(i, "new")
                elif b and i < len(b):
                    if op == 1:
                        del b[i]
                    else:
                        b[i] = "chg"
            self.assertEqual(apply_patch(a, unified(a, b)), b)

    def test_identical_gives_no_patch(self):
        self.assertEqual(unified(["a"], ["a"]), [])


class TestMerge3(unittest.TestCase):
    def test_separate_edits_both_land(self):
        base = list("abcdefgh")
        ours = list("aXcdefgh")
        theirs = list("abcdefYh")
        self.assertEqual(merge3(base, ours, theirs), (list("aXcdefYh"), 0))

    def test_same_edit_is_not_a_conflict(self):
        self.assertEqual(merge3(list("abc"), list("aXc"), list("aXc")), (list("aXc"), 0))

    def test_overlap_conflicts(self):
        lines, n = merge3(list("abc"), list("aXc"), list("aYc"))
        self.assertEqual(n, 1)
        self.assertEqual(lines, ["a", "<<<<<<< ours", "X", "=======", "Y", ">>>>>>> theirs", "c"])

    def test_one_side_unchanged(self):
        self.assertEqual(merge3(list("abc"), list("abc"), list("axbc")), (list("axbc"), 0))

    def test_random_disjoint_edits_never_conflict(self):
        rng = random.Random(3)
        for _ in range(200):
            base = [f"l{i}" for i in range(40)]
            ours, theirs = list(base), list(base)
            for i in rng.sample(range(0, 18), 4):
                ours[i] = f"o{i}"
            for i in rng.sample(range(22, 40), 4):
                theirs[i] = f"t{i}"
            want = ours[:20] + theirs[20:]
            self.assertEqual(merge3(base, ours, theirs), (want, 0))


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.r = Repo.init(self.dir)
        self.r.author = "Tester <t@example.org>"
        self.t = 1000
        def clock():
            self.t += 10
            return self.t
        self.r.clock = clock

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, rel, text):
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.dir, rel), encoding="utf-8") as f:
            return f.read()

    def save(self, msg="c", **files):
        for k, v in files.items():
            self.write(k.replace("__", "/") + ".txt", v)
        self.r.add(["."])
        return self.r.commit_index(msg)


class TestRepo(Base):
    def test_commit_and_log(self):
        a = self.save("one", a="1\n")
        b = self.save("two", a="2\n", sub__b="x\n")
        self.assertEqual([c["id"] for c in self.r.log()], [b, a])
        self.assertEqual(self.r.commit(b)["parents"], [a])
        self.assertEqual(sorted(self.r.tree_files(b)), ["a.txt", "sub/b.txt"])

    def test_nothing_to_commit(self):
        self.save(a="1")
        with self.assertRaises(RepoError):
            self.r.commit_index("again")

    def test_status(self):
        self.save(a="1", b="2")
        self.write("a.txt", "changed")
        self.write("new.txt", "n")
        os.remove(os.path.join(self.dir, "b.txt"))
        s = self.r.status()
        self.assertEqual((s["modified"], s["deleted"], s["untracked"]), (["a.txt"], ["b.txt"], ["new.txt"]))
        self.r.add(["."])
        self.assertEqual(sorted(self.r.status()["staged"]), [("deleted", "b.txt"), ("modified", "a.txt"), ("new", "new.txt")])

    def test_ignore_file(self):
        self.write(".tinygitignore", "*.log\nbuild/\n")
        self.write("a.log", "x")
        self.write("build/o.bin", "x")
        self.write("a.txt", "x")
        self.assertEqual(sorted(self.r.status()["untracked"]), [".tinygitignore", "a.txt"])

    def test_branch_switch_restores_files(self):
        self.save(a="main\n")
        self.r.create_branch("f")
        self.r.switch("f")
        self.save(a="feature\n", extra="e\n")
        self.r.switch("main")
        self.assertEqual(self.read("a.txt"), "main\n")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "extra.txt")))
        self.r.switch("f")
        self.assertEqual(self.read("extra.txt"), "e\n")

    def test_switch_protects_uncommitted_work(self):
        self.save(a="1\n")
        self.r.create_branch("f")
        self.write("a.txt", "dirty")
        with self.assertRaises(RepoError):
            self.r.switch("f")
        self.assertEqual(self.read("a.txt"), "dirty")

    def test_switch_protects_untracked_files(self):
        self.save(a="1\n")
        self.r.create_branch("f")
        self.r.switch("f")
        self.save(b="branch\n")
        self.r.switch("main")
        self.write("b.txt", "mine")
        with self.assertRaises(RepoError):
            self.r.switch("f")
        self.assertEqual(self.read("b.txt"), "mine")

    def test_detached_head_and_revisions(self):
        a = self.save(a="1\n")
        b = self.save(a="2\n")
        self.assertEqual(self.r.resolve("HEAD~1"), a)
        self.assertEqual(self.r.resolve(b[:8]), b)
        self.r.tag("v1", a)
        self.assertEqual(self.r.resolve("v1"), a)
        self.r.switch(a)
        self.assertIsNone(self.r.branch_name())
        self.assertEqual(self.read("a.txt"), "1\n")
        with self.assertRaises(RepoError):
            self.r.resolve("nothing")

    def test_fast_forward(self):
        self.save(a="1\n")
        self.r.create_branch("f")
        self.r.switch("f")
        tip = self.save(a="2\n")
        self.r.switch("main")
        self.assertEqual(self.r.merge("f"), "fast-forward")
        self.assertEqual(self.r.head(), tip)
        self.assertEqual(self.read("a.txt"), "2\n")
        self.assertEqual(self.r.merge("f"), "up-to-date")

    def test_true_merge(self):
        self.save(a="1\n2\n3\n4\n5\n6\n7\n8\n")
        self.r.create_branch("f")
        self.r.switch("f")
        self.save(a="X\n2\n3\n4\n5\n6\n7\n8\n", only_f="f")
        self.r.switch("main")
        self.save(a="1\n2\n3\n4\n5\n6\n7\nY\n", only_main="m")
        self.assertEqual(self.r.merge("f"), "merged")
        self.assertEqual(self.read("a.txt"), "X\n2\n3\n4\n5\n6\n7\nY\n")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "only_f.txt")))
        self.assertEqual(len(self.r.commit(self.r.head())["parents"]), 2)

    def test_conflict_flow(self):
        self.save(a="1\n")
        self.r.create_branch("f")
        self.r.switch("f")
        self.save(a="theirs\n")
        self.r.switch("main")
        self.save(a="ours\n")
        self.assertEqual(self.r.merge("f"), "conflict")
        self.assertIn("<<<<<<< HEAD", self.read("a.txt"))
        with self.assertRaises(RepoError):
            self.r.commit_index("too soon")
        self.write("a.txt", "fixed\n")
        self.r.add(["a.txt"])
        merged = self.r.commit_index("resolved")
        self.assertEqual(len(self.r.commit(merged)["parents"]), 2)
        self.assertEqual(self.r.merge("f"), "up-to-date")

    def test_delete_in_one_branch(self):
        self.save(a="1\n", b="2\n")
        self.r.create_branch("f")
        self.r.switch("f")
        self.r.rm("b.txt")
        self.r.commit_index("rm")
        self.r.switch("main")
        self.save(a="3\n")
        self.assertEqual(self.r.merge("f"), "merged")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "b.txt")))

    def test_branch_delete_rules(self):
        self.save(a="1\n")
        self.r.create_branch("f")
        self.r.switch("f")
        self.save(a="2\n")
        self.r.switch("main")
        with self.assertRaises(RepoError):
            self.r.delete_branch("f")
        self.r.merge("f")
        self.r.delete_branch("f")
        self.assertEqual(self.r.branches(), ["main"])

    def test_diff(self):
        a = self.save(a="1\n2\n")
        b = self.save(a="1\n3\n")
        patch = self.r.diff(a, b)
        self.assertIn("-2", patch)
        self.assertIn("+3", patch)
        self.write("a.txt", "1\n4\n")
        self.assertIn("+4", self.r.diff())

    def test_corruption_is_found(self):
        self.save(a="1\n")
        oid = next(self.r.store.all())
        import zlib
        p = self.r.store._path(oid)
        os.chmod(p, 0o644)
        with open(p, "rb") as f:
            data = zlib.decompress(f.read())
        with open(p, "wb") as f:
            f.write(zlib.compress(data[:-1] + b"Z"))
        self.assertTrue(self.r.verify())


@unittest.skipUnless(HAS_GIT, "git is not installed")
class TestSameBytesAsGit(Base):
    """Our objects must hash exactly as real Git hashes them."""

    def git(self, *args, env=None):
        e = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, **(env or {}))
        return subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args], cwd=self.dir, capture_output=True, text=True, check=True, env=e).stdout.strip()

    def test_blob_tree_and_commit_ids_match(self):
        self.t = 1_700_000_000      # Git rejects dates close to 1970
        self.write("a.txt", "hello\n")
        self.write("dir/b.txt", "world\n")
        self.write("dir-x", "sort order matters\n")
        self.write("dir.txt", "and here\n")
        oid = self.save("first")
        tree = self.r.commit(oid)["tree"]
        self.git("init", "-q")
        self.git("add", "a.txt", "dir", "dir-x", "dir.txt")
        self.assertEqual(self.git("write-tree"), tree)
        when = self.r.commit(oid)["when"]
        date = f"{when} +0000"
        env = {"GIT_AUTHOR_NAME": "Tester", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_NAME": "Tester", "GIT_COMMITTER_EMAIL": "t@example.org", "GIT_COMMITTER_DATE": date}
        self.assertEqual(self.git("commit-tree", tree, "-m", "first", env=env), oid)


class TestFormats(unittest.TestCase):
    def test_tree_round_trip(self):
        e = {"b": ("100644", "a" * 40), "a": ("40000", "b" * 40)}
        self.assertEqual(parse_tree(tree_body(e)), e)

    def test_commit_round_trip(self):
        info = parse_commit(commit_body("a" * 40, ["b" * 40], "N <e@x>", 5, "msg\n\nmore"))
        self.assertEqual((info["tree"], info["parents"], info["author"], info["when"], info["message"]), ("a" * 40, ["b" * 40], "N <e@x>", 5, "msg\n\nmore"))


if __name__ == "__main__":
    unittest.main()
