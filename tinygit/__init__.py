"""TinyGit: version control from scratch."""
from .diff import apply_patch, merge3, myers, unified
from .repo import Repo, RepoError, find_root

__all__ = ["Repo", "RepoError", "find_root", "myers", "unified", "apply_patch", "merge3"]
