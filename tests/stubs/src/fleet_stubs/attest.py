"""The body an `attested` pull request carries: an attestation naming the commit
the pipeline ran on. It is written DURING the `pr` step, so `pr` reads `running`
and `ci` `pending` in every real one.

    python3 attest.py <head sha>
"""

import json
import sys


def body(sha: str) -> str:
    steps = [
        {"step": s, "status": "completed"}
        for s in ("intent", "rebase", "review", "test", "document", "lint", "push")
    ] + [{"step": "pr", "status": "running"}, {"step": "ci", "status": "pending"}]
    payload = json.dumps({"head_sha": sha, "steps": steps})
    return f"<!-- fleet-attestation:v1 {payload} -->\n\nShipped it.\n"


if __name__ == "__main__":
    sys.stdout.write(body(sys.argv[1]))
