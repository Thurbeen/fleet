"""Live regression for SonarCloud's classification of Fleet tests.

Run explicitly; the offline fleet gate must never depend on a cloud service:
    uv run python tests/sonarcloud_e2e.py <analyzed-main-sha>
"""

import argparse
import json
from urllib.parse import urlencode
from urllib.request import urlopen


def verify(revision: str) -> None:
    def get(endpoint: str, **params):
        url = "https://sonarcloud.io/api/" + endpoint + "?" + urlencode(params)
        with urlopen(url, timeout=30) as response:
            return json.load(response)

    actual = get("project_analyses/search", project="Thurbeen_fleet", ps=1)["analyses"][0]["revision"]
    assert actual == revision, f"Analysis is at {actual}, expected {revision}"

    source = get(
        "measures/component", component="Thurbeen_fleet:fleet/cli.py", metricKeys="ncloc"
    )["component"]
    assert any(int(m["value"]) > 0 for m in source["measures"]), "Production Python was not measured"
    test = get(
        "measures/component", component="Thurbeen_fleet:tests/test_cli.py", metricKeys="ncloc"
    )["component"]
    assert not test["measures"], "tests/test_cli.py is counted as production code: sonar.tests is missing"
    print(f"PASS: analysis at {revision} measures production Python and separates tests")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision")
    args = parser.parse_args()
    verify(args.revision)
