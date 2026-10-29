#!/usr/bin/env python3
"""new_worktree_test.py - runtime/new_worktree.sh refuses a branch name with a space (what zsh passes from
`for b in "x y"; do set -- $b; ...`) with exit 2 and a message naming the cause, before creating anything.
Pass: exit 2, message names the branch, no ../port-<name> and no branch. Broken (check-ref-format line removed): git's own
refusal, exit 255 and no zsh cause in the message -> FAIL (2026-10-28)."""
import os, subprocess, sys

here = os.path.dirname(os.path.abspath(__file__))
port = os.path.dirname(here)
name = "nwt test"
r = subprocess.run(["sh", os.path.join(here, "new_worktree.sh"), name], cwd=port, capture_output=True, text=True)
dst = os.path.join(os.path.dirname(port), "port-" + name)
branch = subprocess.run(["git", "-C", port, "rev-parse", "--verify", "-q", "refs/heads/" + name],
                        capture_output=True).returncode == 0
fails = []
if r.returncode != 2: fails.append(f"exit {r.returncode}, want 2")
if f"'{name}' is not a valid branch name (zsh does not split" not in r.stderr: fails.append("message missing: " + r.stderr.strip()[:200])
if os.path.exists(dst): fails.append(dst + " created")
if branch: fails.append("branch created")
r2 = subprocess.run(["sh", os.path.join(here, "new_worktree.sh"), "a", "main", "extra"], cwd=port,
                    capture_output=True, text=True)
if r2.returncode != 2: fails.append(f"three arguments: exit {r2.returncode}, want 2")
print("new_worktree_test: " + ("FAIL: " + "; ".join(fails) if fails else "PASS (space in name and extra argument refused)"))
sys.exit(1 if fails else 0)
