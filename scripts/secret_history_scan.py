"""The history check (step 13): secret-pattern matches in every commit,
`git log -p --all`, with backend/eval/heldout2.yaml excluded (it is never
read).

    python3 scripts/secret_history_scan.py

Prints a count per pattern, then per match: pattern name, commit, +/- side,
path, line number, and one word: placeholder or real?. NEVER PRINTS ANY TEXT
FROM A MATCHED LINE. A "real?" is not a finding: the owner reads the line
and decides. At step 13 it found 4, all placeholders: two dummy sk- keys in
tests/test_embedder.py, and "hunter2", the made-up password of two tests
that a connection error never leaks its URL (read by the owner).
"""

import re
import subprocess
from collections import Counter

PATTERNS = {
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "openai_proj_key": re.compile(r"sk-proj-[A-Za-z0-9_-]{10,}"),
    "postgres_url_password": re.compile(r"postgres(?:ql)?://[^\s/:]+:([^\s@$}]{6,})@"),
    "neon_password": re.compile(r"npg_[A-Za-z0-9]{8,}"),
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    "github_token": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "slack_token": re.compile(r"xox[bp]-"),
}

DUMMIES = {"unused", "password", "postgres", "x", "test", "example", "changeme", "rehearsal", "hunter2"}


def classify(name: str, match: re.Match) -> str:
    if name == "postgres_url_password":
        secret = match.group(1)
    else:
        secret = match.group(0)
    lowered = secret.lower()
    if lowered in DUMMIES or lowered.startswith("${") or lowered.startswith("$"):
        return "placeholder"
    # A token made of one repeated character, or naming itself a dummy.
    body = re.sub(r"^(sk-proj-|sk-|npg_|ghp_|akia)", "", lowered)
    if len(set(body)) <= 1 or any(word in body for word in ("example", "dummy", "fake", "test", "placeholder", "xxxx")):
        return "placeholder"
    return "real?"


log = subprocess.run(
    ["git", "log", "-p", "--all", "--format=COMMIT %H", "--", ".", ":!backend/eval/heldout2.yaml"],
    capture_output=True, text=True, errors="replace", check=True,
).stdout

commit = path = None
old_no = new_no = 0
counts = Counter()
rows = []
for line in log.split("\n"):
    if line.startswith("COMMIT "):
        commit = line[7:19]
        continue
    if line.startswith("+++ "):
        target = line[4:]
        path = target[2:] if target.startswith("b/") else path
        continue
    if line.startswith("--- "):
        source = line[4:]
        path = source[2:] if source.startswith("a/") else path
        continue
    hunk = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
    if hunk:
        old_no, new_no = int(hunk.group(1)), int(hunk.group(2))
        continue
    if line.startswith("+"):
        side, number = "+", new_no
        new_no += 1
    elif line.startswith("-"):
        side, number = "-", old_no
        old_no += 1
    elif line.startswith(" "):
        old_no += 1
        new_no += 1
        continue
    else:
        continue
    for name, pattern in PATTERNS.items():
        for match in pattern.finditer(line):
            counts[name] += 1
            rows.append((name, commit, side, path, number, classify(name, match)))

print("per pattern:", dict(counts) or "none")
print("matched lines:", len({(r[1], r[2], r[3], r[4]) for r in rows}))
for row in rows:
    print(*row)
