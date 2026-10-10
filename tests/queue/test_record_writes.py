"""D3: two writers of one record keep each other's changes.

Every command loads its view of the queue, changes a task and saves the whole
record back. Two of them — the loop's `collect` and a `shepherd`, or the lead
and the loop — each saved what they had loaded, so the second save wrote the
first one's change away. That is how a `shepherd` that loaded a task while it
was `dispatched` put it back to `dispatched` after `collect` had closed it.
"""

import threading

import yaml
from queuekit import ok

from fleet.cli import load
from harness import run_queue as q

queue = load("queue.py")


def a_task() -> str:
    ok(q("topic", "add", "records", "--title", "Records", "--prompt", "fixture"))
    return ok(q("add", "records", "one", "--title", "One", "--repo", "/tmp/repo",
                "--branch", "fix/one")).stdout.strip()


def view(ref: str):
    return queue.Queue(queue.queue_root()).get(ref)


def on_disk(ref: str) -> dict:
    return yaml.safe_load(open(view(ref).file("task.yaml"), encoding="utf-8"))


def test_a_save_keeps_what_another_writer_saved_since_it_loaded():
    ref = a_task()
    shepherd = view(ref)  # loaded while the task is still queued
    collect = view(ref)
    collect.doc["state"] = "landed"
    collect.save()
    shepherd.doc["shepherd"] = {"condition": "green"}
    shepherd.save()
    disk = on_disk(ref)
    assert disk["state"] == "landed", disk
    assert disk["shepherd"] == {"condition": "green"}, disk
    # The saver's own view now holds the merge it wrote.
    assert shepherd.doc["state"] == "landed"


def test_a_key_removed_by_one_writer_stays_removed():
    ref = a_task()
    first = view(ref)
    first.doc["shepherd"] = {"condition": "red"}
    first.save()
    later, clearing = view(ref), view(ref)
    clearing.doc.pop("shepherd")
    clearing.save()
    later.doc["note"] = "kept"
    later.save()
    disk = on_disk(ref)
    assert "shepherd" not in disk and disk["note"] == "kept", disk


def test_concurrent_writers_lose_no_change():
    ref = a_task()
    errors = []

    def writer(n: int) -> None:
        try:
            for i in range(20):
                task = view(ref)
                task.doc[f"writer{n}"] = i
                task.save()
        except Exception as exc:  # noqa: BLE001 - the failure is what is counted
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    disk = on_disk(ref)
    assert [disk.get(f"writer{n}") for n in range(4)] == [19] * 4, disk


def test_a_record_left_unreadable_on_disk_is_still_saved_over():
    """A save over a record that no longer parses writes this view, as it always did."""
    ref = a_task()
    task = view(ref)
    with open(task.file("task.yaml"), "w", encoding="utf-8") as fh:
        fh.write("state: [half an edit\n")
    task.doc["state"] = "landed"
    task.save()
    assert on_disk(ref)["state"] == "landed"
