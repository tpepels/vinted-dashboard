"""Branch cleanup never deletes unmerged, modified or protected branches."""

import unittest

from scripts.cleanup_merged_branches import candidates


class BranchCleanupTests(unittest.TestCase):
    def test_only_exact_merged_pr_head_is_safe(self):
        repo = "tpepels/vinted-dashboard"
        def pr(name, sha, *, merged=True, target="main"):
            return {
                "head": {"ref": name, "sha": sha, "repo": {"full_name": repo}},
                "base": {"ref": target, "repo": {"full_name": repo}},
                "merged_at": "2026-10-12T09:00:00Z" if merged else None,
            }
        branches = [
            {"name": "main", "commit": {"sha": "head"}, "protected": False},
            {"name": "merged", "commit": {"sha": "abc"}, "protected": False},
            {"name": "changed-after-merge", "commit": {"sha": "new"}, "protected": False},
            {"name": "open-pr", "commit": {"sha": "def"}, "protected": False},
            {"name": "protected", "commit": {"sha": "ghi"}, "protected": True},
            {"name": "unknown", "commit": {"sha": "zzz"}, "protected": False},
            {"name": "wrong-base", "commit": {"sha": "123"}, "protected": False},
        ]
        prs = [
            pr("merged", "abc"), pr("changed-after-merge", "old"),
            pr("open-pr", "def", merged=False), pr("protected", "ghi"),
            pr("wrong-base", "123", target="develop"),
        ]
        self.assertEqual(
            [x["name"] for x in candidates(branches, prs, default_branch="main", repository=repo)],
            ["merged"],
        )


if __name__ == "__main__":
    unittest.main()
