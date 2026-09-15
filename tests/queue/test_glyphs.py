"""Claim 17: one setting puts a mark on every session, and takes it back off.

The lead's mark and the workers' are ONE setting, because the reason to turn
either off is the same: this terminal draws a two-cell glyph badly. `on` needs
no file; `off` restores exactly the naming that predates the glyphs; the name is
cut to thurbox's real cap, which is BYTES (`session create` refuses at 65 bytes
with a message that says "64 characters"); and the mark reaches the session
thurbox is actually asked to create.
"""

from queuekit import ok

from harness import expect, queue_module, write
from harness import run_queue as q

GLYPHS = "GLYPHS={}\nLEAD_GLYPH_ON=📡\nLEAD_GLYPH_OFF=⌖\nWORKER_GLYPH_ON=🚀\n"


def glyphs_at(root: str) -> str:
    return queue_module(
        "root = sys.argv[1] or None\n"
        'print(q.glyph_conf(root).get("LEAD_GLYPH_ON", ""), q.worker_glyph(root), sep="\\t")\n',
        root,
    ).rstrip("\n")


def glyph_root(tmp_path, setting: str):
    root = tmp_path / f"glyphs-{setting}"
    write(root / "orchestration" / "session-glyphs.conf", GLYPHS.format(setting))
    return root


def test_with_no_local_file_the_tracked_defaults_answer_with_the_marks():
    assert glyphs_at("") == "📡\t🚀"


def test_glyphs_off_gives_a_worker_no_mark_at_all(tmp_path):
    assert glyphs_at(str(glyph_root(tmp_path, "off"))) == "📡\t"


def test_a_session_name_wears_the_mark_and_is_cut_in_bytes_on_a_codepoint():
    out = queue_module(
        "title = sys.argv[1]\n"
        'print(q.session_name(title, "\\N{ROCKET}"))\n'
        'print(q.session_name(title, ""))\n'
        'print(len(q.session_name("a" * 70, "\\N{ROCKET}").encode()))\n'
        'cut = q.session_name("\\u00e9" * 70, "\\N{ROCKET}")\n'
        'print(len(cut.encode()), cut.encode().decode("utf-8", errors="strict") == cut)\n',
        "Fix the thing",
    ).splitlines()
    assert out[0] == "🚀 Fix the thing"
    # With the setting off the name is the title, unchanged.
    assert out[1] == "Fix the thing"
    assert out[2] == "64"
    size, whole = out[3].split()
    assert int(size) <= 64 and whole == "True", out[3]


def test_the_mark_reaches_the_name_thurbox_is_asked_to_create(tmp_path, queue_dir):
    """The wiring, not only the function. An isolated copy of the default setting,
    so a developer's own gitignored GLYPHS=off cannot make this fail."""
    topic = ok(q("topic", "add", "marked", "--title", "Sessions wear a mark",
                 "--prompt", "give every session a glyph")).stdout.strip()
    ok(q("add", topic, "wear-it", "--title", "Wear the mark", "--repo", "/tmp/repo-a",
         "--branch", "feat/mark", "--number", "01"))
    write(queue_dir / topic / "01-wear-it" / "BRIEF.md", "# Wear the mark\n\nA brief with real content in it.\n")
    out = q("dispatch", "--dry-run", FLEET_GLYPH_ROOT=str(glyph_root(tmp_path, "on"))).out
    expect(out, "🚀 Wear the mark")
