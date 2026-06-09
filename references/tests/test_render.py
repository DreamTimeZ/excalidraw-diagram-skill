"""Regression suite for the Excalidraw renderer.

The guarantees are structural and cross-platform: rendering succeeds, every web font a
diagram uses actually loads (a missing one aborts), rendering touches no real network
host, and bad
input is rejected. A passing render of a text fixture implies its fonts loaded, because the
fatal font gate runs inside render().
"""

import sys
from pathlib import Path

import pytest

import render_excalidraw as rx

TESTS_DIR = Path(__file__).parent
FIXTURES = TESTS_DIR / "fixtures"


def render_fixture(name: str, tmp_path: Path) -> Path:
    out = tmp_path / f"{name}.png"
    rx.render(FIXTURES / f"{name}.excalidraw", out)
    return out


def test_text_fixture_renders(tmp_path):
    # Exit 0 with text present implies the Virgil web font loaded (the fatal gate ran).
    out = render_fixture("text", tmp_path)
    assert out.exists() and out.stat().st_size > 0


def test_cascadia_fixture_renders(tmp_path):
    # fontFamily 3 exercises the Cascadia web font and the gate's Cascadia path.
    out = render_fixture("cascadia", tmp_path)
    assert out.exists() and out.stat().st_size > 0


def test_excalifont_fixture_renders(tmp_path):
    # fontFamily 5 is the Excalidraw 0.18 default (Excalifont).
    out = render_fixture("excalifont", tmp_path)
    assert out.exists() and out.stat().st_size > 0


def test_remaining_font_families_render(tmp_path):
    # fontFamily 6-9 (Nunito, Lilita One, Comic Shanns, Liberation Sans) cover the
    # vendored families no other fixture touches, so a gate/bundle family-name
    # mismatch or a missing vendored file fails here instead of in a user render.
    out = render_fixture("morefonts", tmp_path)
    assert out.exists() and out.stat().st_size > 0


def test_shapes_fixture_renders(tmp_path):
    out = render_fixture("shapes", tmp_path)
    assert out.exists() and out.stat().st_size > 0


def test_render_is_deterministic(tmp_path):
    first = render_fixture("text", tmp_path).read_bytes()
    again = tmp_path / "again.png"
    rx.render(FIXTURES / "text.excalidraw", again)
    assert first == again.read_bytes()


def test_missing_font_aborts(tmp_path, monkeypatch):
    # Force the gate to require a face that can never load, rather than renaming the shared
    # vendored Virgil.woff2 on disk: that mutation is not isolation-safe (a concurrent render
    # collides) and leaves the tree broken if the run is interrupted before restore.
    monkeypatch.setitem(rx.WEB_FONT_FAMILIES, 1, "NoSuchFontFamily")
    with pytest.raises(SystemExit) as exc:
        rx.render(FIXTURES / "text.excalidraw", tmp_path / "out.png")
    assert exc.value.code == 1


def test_cjk_text_aborts(tmp_path):
    # CJK needs the Xiaolai fallback, which is intentionally not vendored. The render
    # must exit 1 rather than let the browser substitute a host OS font, which would
    # produce a platform-dependent diagram with exit 0.
    with pytest.raises(SystemExit) as exc:
        rx.render(FIXTURES / "cjk.excalidraw", tmp_path / "out.png")
    assert exc.value.code == 1


def test_template_references_no_real_cdn():
    # The sentinel host excalidraw-assets.local is intentional: render() serves it from
    # vendor/ via request interception and blocks all other http(s) traffic. What must
    # never reappear in the template is a real CDN host.
    template = (Path(rx.__file__).parent / "render_template.html").read_text(encoding="utf-8")
    assert "excalidraw-assets.local" in template
    for host in ("unpkg.com", "jsdelivr.net", "esm.sh", "esm.run", "cdnjs"):
        assert host not in template


@pytest.mark.parametrize(
    "content",
    [
        "{ not valid json",
        "[]",
        '{"type":"notexcalidraw","elements":[]}',
        '{"type":"excalidraw"}',
        '{"type":"excalidraw","elements":[]}',
        '{"type":"excalidraw","elements":["not-a-dict"]}',
        '{"type":"excalidraw","elements":[42]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"rectangle","isDeleted":true}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"arrow","points":[[0,0,5]]}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"arrow","points":[[NaN,0],[0,5]]}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"arrow","points":[[Infinity,0],[0,5]]}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"line","points":[[true,false]]}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"rectangle","x":Infinity,"y":0,"width":10,"height":10}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"rectangle","x":"10","y":0,"width":10,"height":10}]}',
    ],
)
def test_invalid_input_aborts(tmp_path, content):
    bad = tmp_path / "bad.excalidraw"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        rx.render(bad, tmp_path / "out.png")
    assert exc.value.code == 1


def test_validation_skips_deleted_element_geometry():
    # The renderer and the bounding box both skip deleted elements, so malformed geometry
    # on a deleted element must not reject an otherwise valid file.
    data = {
        "type": "excalidraw",
        "elements": [
            {"id": "a", "type": "arrow", "isDeleted": True, "points": [[float("nan"), 0]]},
            {"id": "b", "type": "rectangle", "x": 0, "y": 0, "width": 10, "height": 10},
        ],
    }
    assert rx.validate_excalidraw(data) == []


def test_required_web_fonts_mapping():
    elements = [
        {"type": "text", "fontFamily": 1},
        {"type": "text", "fontFamily": 3},
        {"type": "text", "fontFamily": 2},
        {"type": "text"},
        {"type": "text", "fontFamily": 1, "isDeleted": True},
        {"type": "rectangle"},
    ]
    # Id 2 (system Helvetica) needs no web font, an element without fontFamily falls
    # back to the Excalifont default (id 5), and deleted elements are skipped.
    assert rx.required_web_fonts(elements) == {"Virgil", "Cascadia", "Excalifont"}


def test_cli_requires_input(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["render_excalidraw.py"])
    with pytest.raises(SystemExit) as exc:
        rx.main()
    assert exc.value.code == 2  # argparse usage error, distinct from a render failure (1)


@pytest.mark.parametrize("flag,value", [("--scale", "0"), ("--scale", "-1"), ("--width", "0")])
def test_cli_rejects_non_positive_dimensions(monkeypatch, flag, value):
    # --scale 0 otherwise exits 0 with a wrong-resolution PNG (Chromium treats a device
    # scale factor of 0 as no override), so it must die as a usage error before rendering.
    monkeypatch.setattr(sys, "argv", ["render_excalidraw.py", "x.excalidraw", flag, value])
    with pytest.raises(SystemExit) as exc:
        rx.main()
    assert exc.value.code == 2


def test_cli_missing_file_aborts(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["render_excalidraw.py", str(tmp_path / "nope.excalidraw")])
    with pytest.raises(SystemExit) as exc:
        rx.main()
    assert exc.value.code == 1


def test_cli_renders_and_prints_path(monkeypatch, tmp_path, capsys):
    out = tmp_path / "cli.png"
    monkeypatch.setattr(sys, "argv", ["render_excalidraw.py", str(FIXTURES / "text.excalidraw"), "-o", str(out)])
    rx.main()
    assert out.exists() and out.stat().st_size > 0
    assert str(out) in capsys.readouterr().out


def test_cli_check_succeeds(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["render_excalidraw.py", "--check"])
    rx.main()  # run_self_check exits nonzero on any failure, so returning means healthy
    assert "OK:" in capsys.readouterr().out


def test_cli_check_rejects_input(monkeypatch):
    # --check silently ignoring a positional input would read as "that file rendered".
    monkeypatch.setattr(sys, "argv", ["render_excalidraw.py", "x.excalidraw", "--check"])
    with pytest.raises(SystemExit) as exc:
        rx.main()
    assert exc.value.code == 2
