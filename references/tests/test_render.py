"""Regression suite for the Excalidraw renderer.

The guarantees are structural and cross-platform: rendering succeeds, every web font a
diagram uses actually loads (a missing one aborts), rendering touches no real network
host, and bad
input is rejected. A passing render of a text fixture implies its fonts loaded, because the
fatal font gate runs inside render().
"""

import json
import struct
import sys
import zlib
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


# Channel bounds for theme checks: the white canvas becomes about #121212 under
# Excalidraw's invert(93%) dark filter, and stays #ffffff in an explicit light render.
DARK_CHANNEL_MAX = 60
LIGHT_CHANNEL_MIN = 200


def first_pixel_rgb(png_path: Path) -> tuple[int, int, int]:
    # Stdlib-only PNG peek: for the first pixel of the first scanline every PNG filter
    # type reduces to the raw bytes (the left/up/upper-left predecessors are all zero),
    # so the pixel is readable without unfiltering or an image library.
    raw = png_path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    idat = bytearray()
    pos = 8
    while pos < len(raw):
        length = int.from_bytes(raw[pos:pos + 4], "big")
        chunk_type = raw[pos + 4:pos + 8]
        if chunk_type == b"IHDR":
            _, _, bit_depth, color_type = struct.unpack(">IIBB", raw[pos + 8:pos + 18])
            assert bit_depth == 8 and color_type in (2, 6)  # RGB or RGBA
        elif chunk_type == b"IDAT":
            idat += raw[pos + 8:pos + 8 + length]
        elif chunk_type == b"IEND":
            break
        pos += length + 12
    scanline = zlib.decompress(bytes(idat))
    return tuple(scanline[1:4])  # byte 0 is the row's filter type


def test_render_defaults_to_dark_mode(tmp_path):
    # The fixture's appState has no exportWithDarkMode, so the template default must
    # kick in and the corner background pixel must come out dark, not white.
    out = render_fixture("text", tmp_path)
    assert all(c <= DARK_CHANNEL_MAX for c in first_pixel_rgb(out))


def test_explicit_light_mode_overrides_dark_default(tmp_path):
    # "exportWithDarkMode": false in the file must win over the renderer's dark default.
    data = json.loads((FIXTURES / "text.excalidraw").read_text(encoding="utf-8"))
    data["appState"]["exportWithDarkMode"] = False
    src = tmp_path / "light.excalidraw"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "light.png"
    rx.render(src, out)
    assert all(c >= LIGHT_CHANNEL_MIN for c in first_pixel_rgb(out))


def test_dark_default_applies_without_appstate(tmp_path):
    # A file with no appState at all takes the template's `data.appState || {}` branch;
    # the dark default must apply there too, not only when appState lacks the key.
    data = json.loads((FIXTURES / "text.excalidraw").read_text(encoding="utf-8"))
    del data["appState"]
    src = tmp_path / "noappstate.excalidraw"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "noappstate.png"
    rx.render(src, out)
    assert all(c <= DARK_CHANNEL_MAX for c in first_pixel_rgb(out))


def test_skeleton_freedraw_aborts(tmp_path, capsys):
    # A freedraw element without its pressures/simulatePressure fields makes the bundle
    # throw while drawing it; Excalidraw swallows that into console.error and delivers
    # an SVG without the stroke. The template's console-error gate must turn that into
    # an abort instead of a PNG that silently lost an element. Every abort path exits 1,
    # so only the message assertion pins the failure to that gate.
    elements = [
        {"id": "r", "type": "rectangle", "x": 0, "y": 0, "width": 200, "height": 100, "seed": 7},
        {"id": "f", "type": "freedraw", "x": 20, "y": 20, "width": 100, "height": 60,
         "strokeColor": "#e03131", "strokeWidth": 4, "seed": 8,
         "points": [[0, 0], [50, 30], [100, 60]]},
    ]
    src = tmp_path / "skeleton.excalidraw"
    src.write_text(json.dumps({"type": "excalidraw", "elements": elements}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        rx.render(src, tmp_path / "out.png")
    assert exc.value.code == 1
    assert "element(s) failed to render" in capsys.readouterr().err


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
        '{"type":"excalidraw","elements":[{"id":"a","type":"text","text":"x","fontFamily":"3"}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"text","text":"x","fontFamily":4}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"text","text":"x","fontFamily":2}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"text","text":"x","fontFamily":true}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"text","text":"x","fontFamily":null}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"arrow","points":[]}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"arrow","points":[[0,0]]}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"freedraw","points":[[0,0],[NaN,5]]}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"freedraw","points":[]}]}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"rectangle","x":0,"y":0,"width":10,"height":10}],"appState":{"exportWithDarkMode":"false"}}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"rectangle","x":0,"y":0,"width":10,"height":10}],"appState":{"exportWithDarkMode":1}}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"rectangle","x":0,"y":0,"width":10,"height":10}],"appState":"oops"}',
        '{"type":"excalidraw","elements":[{"id":"a","type":"rectangle","x":0,"y":0,"width":10,"height":10}],"appState":[]}',
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


def test_validation_accepts_every_known_font_id():
    # Acceptance boundary for the fontFamily gate, pinned to a literal: elements built
    # from VALID_FONT_FAMILIES alone would shrink with the set, so only the literal
    # makes a regression dropping an id fail here, pointedly, instead of in a render
    # fixture. The element without fontFamily pins DEFAULT_FONT_FAMILY itself
    # staying inside the valid set.
    assert rx.VALID_FONT_FAMILIES == frozenset({1, 3, 5, 6, 7, 8, 9})
    elements = [
        {"id": f"t{i}", "type": "text", "text": "x", "fontFamily": i}
        for i in sorted(rx.VALID_FONT_FAMILIES)
    ]
    elements.append({"id": "tdefault", "type": "text", "text": "x"})
    assert rx.validate_excalidraw({"type": "excalidraw", "elements": elements}) == []


def test_validation_accepts_single_point_freedraw():
    # A one-point freedraw is a legal dot, so the two-point arrow/line minimum must not
    # apply to it. Guards the per-type minimum in MIN_POINTS.
    data = {
        "type": "excalidraw",
        "elements": [{"id": "f", "type": "freedraw", "x": 0, "y": 0, "width": 1, "height": 1, "points": [[0, 0]]}],
    }
    assert rx.validate_excalidraw(data) == []


def test_required_web_fonts_mapping():
    elements = [
        {"type": "text", "fontFamily": 1},
        {"type": "text", "fontFamily": 3},
        {"type": "text"},
        {"type": "text", "fontFamily": 1, "isDeleted": True},
        {"type": "rectangle"},
    ]
    # An element without fontFamily falls back to the Excalifont default (id 5) and
    # deleted elements are skipped.
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


# --- Obsidian .excalidraw.md extraction ---------------------------------------------

def raw_excalidraw_md(scene: dict) -> str:
    # An Obsidian .excalidraw.md in 'raw' plugin mode: the scene is a plain 'json' fence
    # under the '## Drawing' heading. Compressed-mode extraction is covered by the
    # committed text.excalidraw.md fixture (its base64 needs the plugin's LZString, which
    # is deliberately not a project dependency).
    return (
        "---\nexcalidraw-plugin: raw\n---\n\n"
        "# Excalidraw Data\n\n## Text Elements\nok ^a\n\n## Drawing\n"
        f"```json\n{json.dumps(scene)}\n```\n%%"
    )


def test_compressed_fixture_decodes_to_text_scene():
    # The committed compressed-json fixture must decode, byte for byte, to the same scene
    # as text.excalidraw. This pins both the vendored LZString port and the fixture itself:
    # regenerating the fixture wrong (or a codec regression) fails here.
    got = json.loads(rx.load_scene(FIXTURES / "text.excalidraw.md"))
    expected = json.loads((FIXTURES / "text.excalidraw").read_text(encoding="utf-8"))
    assert got == expected


def test_extract_raw_json_drawing_block():
    scene = {"type": "excalidraw", "elements": [{"id": "r", "type": "rectangle"}]}
    assert json.loads(rx.extract_excalidraw_md(raw_excalidraw_md(scene))) == scene


def test_extract_ignores_text_elements_section():
    # The '## Text Elements' labels have no geometry; the scene must come from '## Drawing'.
    # A label that looks like JSON above the real block must not be picked up.
    scene = {"type": "excalidraw", "elements": [{"id": "r", "type": "rectangle"}]}
    md = raw_excalidraw_md(scene).replace("ok ^a", '{"type":"excalidraw","elements":[]} ^a')
    assert json.loads(rx.extract_excalidraw_md(md)) == scene


def test_extract_missing_drawing_block_aborts(capsys):
    with pytest.raises(SystemExit) as exc:
        rx.extract_excalidraw_md("# Notes\n\nno drawing here\n")
    assert exc.value.code == 1
    assert "## Drawing" in capsys.readouterr().err


def test_extract_corrupt_compressed_block_aborts(capsys):
    # An empty (or otherwise unrecoverable) compressed payload must abort with a message,
    # not raise a raw KeyError/IndexError from the decoder.
    for payload in ("", "!!!not-base64!!!", "N4Kg"):  # empty, bad alphabet, truncated
        md = f"## Drawing\n```compressed-json\n{payload}\n```\n%%"
        with pytest.raises(SystemExit) as exc:
            rx.extract_excalidraw_md(md)
        assert exc.value.code == 1
        assert "decompress" in capsys.readouterr().err


def test_render_excalidraw_md_end_to_end(tmp_path):
    # Full pipeline on a compressed Obsidian file: the scene must be extracted, decompressed,
    # validated, and rendered (the text fixture's font gate runs, so a green render proves
    # the fonts loaded too). Default output strips the whole .excalidraw.md suffix.
    out = rx.render(FIXTURES / "text.excalidraw.md", tmp_path / "diagram.png")
    assert out.exists() and out.stat().st_size > 0


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_extract_handles_windows_line_endings(newline):
    # Obsidian files authored/synced on Windows carry CRLF; the LF-anchored '## Drawing'
    # regex must still match after normalization. Both compressed and raw forms.
    lf = (FIXTURES / "text.excalidraw.md").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES / "text.excalidraw").read_text(encoding="utf-8"))
    assert json.loads(rx.extract_excalidraw_md(lf.replace("\n", newline))) == expected
    scene = {"type": "excalidraw", "elements": [{"id": "r", "type": "rectangle"}]}
    raw = raw_excalidraw_md(scene).replace("\n", newline)
    assert json.loads(rx.extract_excalidraw_md(raw)) == scene


# --- Theme selection: --dark / --both -----------------------------------------------

def test_light_flag_overrides_dark_default(tmp_path):
    # dark=False must beat the renderer's dark default, so the corner comes out light.
    out = tmp_path / "l.png"
    rx.render(FIXTURES / "text.excalidraw", out, dark=False)
    assert all(c >= LIGHT_CHANNEL_MIN for c in first_pixel_rgb(out))


def test_dark_flag_overrides_light_file(tmp_path):
    # dark=True must beat an in-file exportWithDarkMode: false, so the corner comes out dark.
    data = json.loads((FIXTURES / "text.excalidraw").read_text(encoding="utf-8"))
    data["appState"]["exportWithDarkMode"] = False
    src = tmp_path / "d.excalidraw"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "d.png"
    rx.render(src, out, dark=True)
    assert all(c <= DARK_CHANNEL_MAX for c in first_pixel_rgb(out))


def test_dark_flag_injects_appstate_when_absent(tmp_path):
    # A file with no appState at all must still honor a forced theme (the injection path
    # replaces a missing/None appState with a fresh dict rather than crashing).
    data = json.loads((FIXTURES / "text.excalidraw").read_text(encoding="utf-8"))
    del data["appState"]
    src = tmp_path / "n.excalidraw"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "n.png"
    rx.render(src, out, dark=False)
    assert all(c >= LIGHT_CHANNEL_MIN for c in first_pixel_rgb(out))


def test_cli_both_writes_two_themed_pngs(monkeypatch, tmp_path, capsys):
    # --both writes -light and -dark from one invocation, each in its own theme, and prints
    # both paths. This is the whole point: no second pass, no rename.
    base = tmp_path / "diagram"
    monkeypatch.setattr(sys, "argv", [
        "render_excalidraw.py", str(FIXTURES / "text.excalidraw"),
        "-o", str(base.with_suffix(".png")), "--both",
    ])
    rx.main()
    light, dark = tmp_path / "diagram-light.png", tmp_path / "diagram-dark.png"
    assert light.exists() and dark.exists()
    assert all(c >= LIGHT_CHANNEL_MIN for c in first_pixel_rgb(light))
    assert all(c <= DARK_CHANNEL_MAX for c in first_pixel_rgb(dark))
    printed = capsys.readouterr().out
    assert str(light) in printed and str(dark) in printed


def test_cli_both_default_naming_strips_md_suffix(monkeypatch, tmp_path):
    # Without -o, --both derives names from the input, stripping the full .excalidraw.md
    # suffix: note.excalidraw.md -> note-light.png / note-dark.png, not note.excalidraw-*.
    src = tmp_path / "note.excalidraw.md"
    src.write_text((FIXTURES / "text.excalidraw.md").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["render_excalidraw.py", str(src), "--both"])
    rx.main()
    assert (tmp_path / "note-light.png").exists()
    assert (tmp_path / "note-dark.png").exists()


def test_cli_both_output_base_keeps_dotted_name(monkeypatch, tmp_path):
    # -o for --both drops only a .png extension: a dotted base like report.v2 must yield
    # report.v2-light.png, not report-light.png (with_suffix("") would over-strip it).
    base = tmp_path / "report.v2"
    monkeypatch.setattr(sys, "argv", [
        "render_excalidraw.py", str(FIXTURES / "text.excalidraw"), "-o", str(base), "--both",
    ])
    rx.main()
    assert (tmp_path / "report.v2-light.png").exists()
    assert (tmp_path / "report.v2-dark.png").exists()


@pytest.mark.parametrize("name,stem", [
    ("foo.excalidraw", "foo"),
    ("foo.excalidraw.md", "foo"),
    ("my.v2.excalidraw", "my.v2"),
    ("a.b.excalidraw.md", "a.b"),
])
def test_strip_scene_suffix(name, stem):
    assert rx._strip_scene_suffix(Path("/x") / name) == Path("/x") / stem


def test_cli_light_flag_renders_light(monkeypatch, tmp_path):
    # --light must wire through to render(dark=False): the corner comes out light even
    # though the renderer's default is dark.
    out = tmp_path / "l.png"
    monkeypatch.setattr(sys, "argv", [
        "render_excalidraw.py", str(FIXTURES / "text.excalidraw"), "-o", str(out), "--light",
    ])
    rx.main()
    assert all(c >= LIGHT_CHANNEL_MIN for c in first_pixel_rgb(out))


def test_cli_dark_flag_renders_dark(monkeypatch, tmp_path):
    # --dark must wire through to render(dark=True), overriding an in-file light setting so
    # the corner comes out dark. Mirrors the --light CLI test.
    data = json.loads((FIXTURES / "text.excalidraw").read_text(encoding="utf-8"))
    data["appState"]["exportWithDarkMode"] = False
    src = tmp_path / "d.excalidraw"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "d.png"
    monkeypatch.setattr(sys, "argv", ["render_excalidraw.py", str(src), "-o", str(out), "--dark"])
    rx.main()
    assert all(c <= DARK_CHANNEL_MAX for c in first_pixel_rgb(out))


@pytest.mark.parametrize("flags", [["--dark", "--both"], ["--dark", "--light"], ["--light", "--both"]])
def test_cli_theme_flags_are_mutually_exclusive(monkeypatch, flags):
    monkeypatch.setattr(sys, "argv", ["render_excalidraw.py", "x.excalidraw", *flags])
    with pytest.raises(SystemExit) as exc:
        rx.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("flag", ["--dark", "--light", "--both"])
def test_cli_check_rejects_theme_flags(monkeypatch, flag):
    # --check with a theme flag would read as "the fixture rendered in that theme".
    monkeypatch.setattr(sys, "argv", ["render_excalidraw.py", "--check", flag])
    with pytest.raises(SystemExit) as exc:
        rx.main()
    assert exc.value.code == 2


# --- Regression: final '## Drawing' block wins; uppercase --both -o suffix stripped ----

def test_extract_picks_final_drawing_block_over_earlier_decoy():
    # A stray '## Drawing' fence in note content above the plugin's real block must be
    # ignored: Obsidian always writes the scene as the file's final '## Drawing' section.
    decoy = {"type": "excalidraw", "elements": [{"id": "DECOY", "type": "rectangle"}]}
    real = {"type": "excalidraw", "elements": [{"id": "REAL", "type": "rectangle"}]}
    md = (
        "# Notes\n\n## Drawing\n"
        f"```json\n{json.dumps(decoy)}\n```\n\n"
        "# Excalidraw Data\n\n## Text Elements\nok ^a\n\n## Drawing\n"
        f"```json\n{json.dumps(real)}\n```\n%%"
    )
    assert json.loads(rx.extract_excalidraw_md(md))["elements"][0]["id"] == "REAL"


def test_cli_both_output_base_strips_uppercase_png(monkeypatch, tmp_path):
    # -o with an uppercase .PNG extension must be stripped like .png, not kept as a dotted
    # base: diagram.PNG -> diagram-light.png, not diagram.PNG-light.png.
    base = tmp_path / "diagram.PNG"
    monkeypatch.setattr(sys, "argv", [
        "render_excalidraw.py", str(FIXTURES / "text.excalidraw"), "-o", str(base), "--both",
    ])
    rx.main()
    assert (tmp_path / "diagram-light.png").exists()
    assert (tmp_path / "diagram-dark.png").exists()


# --- Foreground-aware theme checks (full PNG decode, not just the corner) -------------

def _decode_png_full(png_path: Path):
    # Unlike first_pixel_rgb (first pixel only), this unfilters every scanline so any
    # interior/foreground pixel is readable with the stdlib alone. Returns
    # (width, height, channels, raw_rgb(a)_bytes).
    raw = png_path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    idat = bytearray()
    pos = 8
    width = height = color_type = None
    while pos < len(raw):
        length = int.from_bytes(raw[pos:pos + 4], "big")
        chunk = raw[pos + 4:pos + 8]
        if chunk == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", raw[pos + 8:pos + 18])
            assert bit_depth == 8 and color_type in (2, 6)
        elif chunk == b"IDAT":
            idat += raw[pos + 8:pos + 8 + length]
        elif chunk == b"IEND":
            break
        pos += length + 12
    channels = 4 if color_type == 6 else 3
    data = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    p = 0

    def paeth(a, b, c):
        pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
        return a if pa <= pb and pa <= pc else b if pb <= pc else c

    for _ in range(height):
        ftype = data[p]
        line = bytearray(data[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                line[i] = (line[i] + paeth(a, b, c)) & 0xFF
        out += line
        prev = line
    return width, height, channels, out


def _theme_pixel_fractions(png_path: Path) -> tuple[float, float]:
    # (light_fraction, dark_fraction) over all pixels, reusing the corner-check bounds.
    width, height, channels, px = _decode_png_full(png_path)
    total = width * height
    light = sum(1 for i in range(0, len(px), channels)
                if all(px[i + k] >= LIGHT_CHANNEL_MIN for k in range(3)))
    dark = sum(1 for i in range(0, len(px), channels)
               if all(px[i + k] <= DARK_CHANNEL_MAX for k in range(3)))
    return light / total, dark / total


# Measured fractions for the text fixture: light render (L=0.22, D=0.09), dark render
# (L=0.09, D=0.22). These bounds sit well inside those with margin.
THEME_BG_MIN_FRACTION = 0.15
THEME_INK_MIN_FRACTION = 0.03


def test_dark_render_inverts_foreground_ink(tmp_path):
    # The corner-pixel theme tests only prove the background inverted. This proves the ink
    # did too: a dark render must have a dark-background majority AND a light-ink minority
    # (the fixture's dark strokes filtered to light).
    out = tmp_path / "d.png"
    rx.render(FIXTURES / "text.excalidraw", out, dark=True)
    light, dark = _theme_pixel_fractions(out)
    assert dark > THEME_BG_MIN_FRACTION, f"dark background missing (dark={dark})"
    assert light > THEME_INK_MIN_FRACTION, f"ink did not invert to light (light={light})"


def test_light_render_keeps_foreground_ink_dark(tmp_path):
    # Mirror: a light render keeps a white-background majority and a dark-ink minority.
    out = tmp_path / "l.png"
    rx.render(FIXTURES / "text.excalidraw", out, dark=False)
    light, dark = _theme_pixel_fractions(out)
    assert light > THEME_BG_MIN_FRACTION, f"white background missing (light={light})"
    assert dark > THEME_INK_MIN_FRACTION, f"ink did not stay dark (dark={dark})"
