"""Render Excalidraw JSON to PNG using Playwright + headless Chromium.

React and Excalidraw are vendored as a single esbuild bundle under vendor/ (rebuild it
with build_vendor.sh) and loaded from disk. Font requests are served from vendor/ via
request interception while every other http(s) request is blocked, so rendering never
touches the network. A font that fails to load aborts the render rather than silently
producing a fallback-font diagram.

Usage:
    cd .claude/skills/excalidraw-diagram/references
    uv run python render_excalidraw.py <path-to-file.excalidraw> [--output path.png] [--scale 2] [--width 1920]
    uv run python render_excalidraw.py --check   # verify the pipeline end to end

First-time setup:
    cd .claude/skills/excalidraw-diagram/references
    uv sync
    uv run playwright install chromium
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from urllib.parse import urlparse

MODULE_LOAD_TIMEOUT_MS = 30_000
RENDER_TIMEOUT_MS = 15_000
DEFAULT_SCALE = 2
DEFAULT_MAX_WIDTH = 1920
VIEWPORT_PADDING = 80
MIN_VIEWPORT_HEIGHT = 600
EMPTY_BOUNDING_BOX = (0, 0, 800, 600)
# Excalidraw 0.18.1 font-family ids that map to a vendored web font. Ids 2 (host
# Helvetica) and 4 (never assigned by Excalidraw) have no vendored asset and are
# rejected by validation, and the automatic Xiaolai CJK fallback is intentionally not
# vendored: requesting its subsets makes render() abort (see the missing-asset check)
# instead of silently degrading.
WEB_FONT_FAMILIES = {
    1: "Virgil",
    3: "Cascadia",
    5: "Excalifont",
    6: "Nunito",
    7: "Lilita One",
    8: "Comic Shanns",
    9: "Liberation Sans",
}
DEFAULT_FONT_FAMILY = 5
# Ids validation accepts: exactly the vendored web fonts above. Anything else makes
# the output platform-dependent with exit 0 (id 2: each host OS substitutes its own
# Helvetica, id 4 and string-typed ids: the bundle silently falls back to a serif), so
# validation rejects it up front. Derived from WEB_FONT_FAMILIES so the two cannot drift.
VALID_FONT_FAMILIES = frozenset(WEB_FONT_FAMILIES)
GEOMETRY_FIELDS = ("x", "y", "width", "height")
# Types whose 'points' are validated, with the minimum count that draws anything (a
# segment for arrow/line, a dot for freedraw). Freedraw is included because a NaN
# coordinate makes the bundle silently drop the whole element with exit 0.
MIN_POINTS = {"arrow": 2, "line": 2, "freedraw": 1}
# The template points EXCALIDRAW_ASSET_PATH at this sentinel host. render() serves it
# from vendor/ and blocks all other http(s) traffic, so the bundle's baked-in CDN asset
# fallback can never reach the network.
ASSET_HOST = "https://excalidraw-assets.local"


def _is_finite_number(value: object) -> bool:
    """True for finite int/float. Rejects bool (an int subclass) and NaN/Infinity, which
    json.loads accepts by default; NaN geometry renders a silently corrupted diagram and
    Infinity crashes the viewport computation."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _valid_points(points: object, minimum: int) -> bool:
    """True if points is a list of at least `minimum` [x, y] finite-number pairs (what
    compute_bounding_box unpacks for arrow/line; fewer than the minimum draws nothing)."""
    return isinstance(points, list) and len(points) >= minimum and all(
        isinstance(p, (list, tuple)) and len(p) == 2 and all(_is_finite_number(c) for c in p)
        for p in points
    )


def validate_excalidraw(data: object) -> list[str]:
    """Validate Excalidraw JSON structure. Returns list of errors (empty = valid)."""
    if not isinstance(data, dict):
        return [f"Expected a JSON object, got {type(data).__name__}"]

    errors: list[str] = []

    if data.get("type") != "excalidraw":
        errors.append(f"Expected type 'excalidraw', got '{data.get('type')}'")

    if "elements" not in data:
        errors.append("Missing 'elements' array")
    elif not isinstance(data["elements"], list):
        errors.append("'elements' must be an array")
    elif not all(isinstance(el, dict) for el in data["elements"]):
        errors.append("'elements' must contain only objects")
    elif not any(not el.get("isDeleted") for el in data["elements"]):
        errors.append("'elements' array has nothing to render (empty or all deleted)")
    else:
        for el in data["elements"]:
            # Deleted elements are skipped by the renderer and the bounding box alike, so
            # their geometry is never consumed and must not fail validation.
            if el.get("isDeleted"):
                continue
            bad_fields = [f for f in GEOMETRY_FIELDS if f in el and not _is_finite_number(el[f])]
            if bad_fields:
                errors.append(f"element '{el.get('id', '?')}' has non-finite or non-numeric {', '.join(bad_fields)}")
                break
            min_points = MIN_POINTS.get(el.get("type"))
            if min_points is not None and "points" in el and not _valid_points(el["points"], min_points):
                errors.append(f"element '{el.get('id', '?')}' has malformed 'points' (expected at least {min_points} finite [x, y] pairs)")
                break
            if el.get("type") == "text":
                fam = el.get("fontFamily", DEFAULT_FONT_FAMILY)
                if not isinstance(fam, int) or isinstance(fam, bool) or fam not in VALID_FONT_FAMILIES:
                    errors.append(f"element '{el.get('id', '?')}' has unknown fontFamily {fam!r} (valid ids: {sorted(VALID_FONT_FAMILIES)})")
                    break

    return errors


def compute_bounding_box(elements: list[dict]) -> tuple[float, float, float, float]:
    """Compute bounding box (min_x, min_y, max_x, max_y) across all elements."""
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")

    for el in elements:
        if el.get("isDeleted"):
            continue
        x = el.get("x", 0)
        y = el.get("y", 0)
        w = el.get("width", 0)
        h = el.get("height", 0)

        # For arrows/lines, points array defines the shape relative to x,y
        if el.get("type") in ("arrow", "line") and "points" in el:
            for px, py in el["points"]:
                min_x = min(min_x, x + px)
                min_y = min(min_y, y + py)
                max_x = max(max_x, x + px)
                max_y = max(max_y, y + py)
        else:
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x + abs(w))
            max_y = max(max_y, y + abs(h))

    if min_x == float("inf"):
        return EMPTY_BOUNDING_BOX

    return (min_x, min_y, max_x, max_y)


def required_web_fonts(elements: list[dict]) -> set[str]:
    """Web-font family names this diagram needs (empty if it contains no text)."""
    families: set[str] = set()
    for el in elements:
        if el.get("type") == "text" and not el.get("isDeleted"):
            name = WEB_FONT_FAMILIES.get(el.get("fontFamily", DEFAULT_FONT_FAMILY))
            if name:
                families.add(name)
    return families


def render(
    excalidraw_path: Path,
    output_path: Path | None = None,
    scale: int = DEFAULT_SCALE,
    max_width: int = DEFAULT_MAX_WIDTH,
) -> Path:
    """Render an .excalidraw file to PNG. Returns the output PNG path."""
    # Import playwright here so validation errors show before import errors
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed.", file=sys.stderr)
        print("Run: cd .claude/skills/excalidraw-diagram/references && uv sync && uv run playwright install chromium", file=sys.stderr)
        sys.exit(1)

    # Read and validate
    raw = excalidraw_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {excalidraw_path}: {e}", file=sys.stderr)
        sys.exit(1)

    errors = validate_excalidraw(data)
    if errors:
        print(f"ERROR: Invalid Excalidraw file:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # Compute viewport size from element bounding box
    elements = [e for e in data["elements"] if not e.get("isDeleted")]
    min_x, min_y, max_x, max_y = compute_bounding_box(elements)
    diagram_w = max_x - min_x + VIEWPORT_PADDING * 2
    diagram_h = max_y - min_y + VIEWPORT_PADDING * 2

    # Cap viewport width, let height be natural
    vp_width = min(int(diagram_w), max_width)
    vp_height = max(int(diagram_h), MIN_VIEWPORT_HEIGHT)

    # Output path
    if output_path is None:
        output_path = excalidraw_path.with_suffix(".png")

    # Template path (same directory as this script)
    template_path = Path(__file__).parent / "render_template.html"
    if not template_path.exists():
        print(f"ERROR: Template not found at {template_path}", file=sys.stderr)
        sys.exit(1)

    template_url = template_path.as_uri()

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            if "Executable doesn't exist" in str(e) or "browserType.launch" in str(e):
                print("ERROR: Chromium not installed for Playwright.", file=sys.stderr)
                print("Run: cd .claude/skills/excalidraw-diagram/references && uv run playwright install chromium", file=sys.stderr)
                sys.exit(1)
            raise

        with browser:
            # Belt and braces on top of the route blockers: offline=True makes Chromium
            # fail anything that reaches the real network stack (route-fulfilled
            # requests never do), which also covers WebSockets, and service workers
            # (whose fetches bypass page routes) are blocked outright.
            context = browser.new_context(
                viewport={"width": vp_width, "height": vp_height},
                device_scale_factor=scale,
                offline=True,
                service_workers="block",
            )
            page = context.new_page()

            # Offline enforcement: block every real http(s) request and serve the
            # sentinel asset host from vendor/ instead. file:// URLs (the template and
            # the bundle) are not routed. Playwright matches routes in reverse
            # registration order, so the catch-all blockers go first and the sentinel
            # route registered last wins for font requests.
            vendor_dir = (Path(__file__).parent / "vendor").resolve()
            blocked_requests: list[str] = []
            missing_assets: list[str] = []

            def _block(route):
                blocked_requests.append(route.request.url)
                route.abort()

            def _serve_vendored_asset(route):
                rel = urlparse(route.request.url).path.lstrip("/")
                asset = (vendor_dir / rel).resolve()
                if asset.is_file() and asset.is_relative_to(vendor_dir):
                    route.fulfill(status=200, body=asset.read_bytes(), content_type="font/woff2")
                else:
                    # An asset Excalidraw wants but vendor/ does not have, in practice a
                    # Xiaolai subset for CJK text. Recording it fails the render below
                    # instead of letting the browser substitute a host OS font.
                    missing_assets.append(rel)
                    route.abort()

            page.route("http://**", _block)
            page.route("https://**", _block)
            page.route(f"{ASSET_HOST}/**", _serve_vendored_asset)

            # Load the template
            page.goto(template_url)

            # Wait for the vendored React/Excalidraw bundles to load and expose ExcalidrawLib.
            # __moduleReady is null while loading, true on success, false on failure, so a load
            # error fails fast with the captured reason instead of an opaque 30s timeout.
            try:
                page.wait_for_function("window.__moduleReady !== null", timeout=MODULE_LOAD_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                err = page.evaluate("window.__moduleError") or "bundle load timed out"
                print(f"ERROR: {err}", file=sys.stderr)
                sys.exit(1)
            if not page.evaluate("window.__moduleReady"):
                err = page.evaluate("window.__moduleError") or "bundle failed to load"
                print(f"ERROR: {err}", file=sys.stderr)
                sys.exit(1)

            # Launch the render without awaiting its promise, then poll for settlement.
            # renderDiagram sets __renderComplete in both its success and failure branches, so
            # the bounded wait resolves either way; a hang inside exportToSvg never sets it and
            # becomes a stated timeout instead of blocking forever (page.evaluate has no timeout
            # of its own). The verdict is then read from __renderError.
            page.evaluate("(d) => { void window.renderDiagram(d); }", data)
            try:
                page.wait_for_function("window.__renderComplete === true", timeout=RENDER_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                print("ERROR: Render timed out.", file=sys.stderr)
                sys.exit(1)

            render_error = page.evaluate("window.__renderError")
            if render_error:
                print(f"ERROR: Render failed: {render_error}", file=sys.stderr)
                sys.exit(1)

            # Correctness gate: require a *loaded* face for every web font this diagram
            # uses. Excalidraw 0.18 registers faces lazily for just the fonts a render
            # touches, and a face whose fetch was blocked or failed never reaches status
            # 'loaded', so this catches any font that silently fell back. A face in
            # status 'error' (served but unparsable) also fails its family: its glyphs
            # fall back even when a sibling unicode-range subset loaded fine.
            required = required_web_fonts(elements)
            if required:
                missing = page.evaluate(
                    "async (fams) => { try { await document.fonts.ready; } catch (e) {} "
                    "return fams.filter(fam => { "
                    "const faces = [...document.fonts].filter(f => f.family === fam); "
                    "return !faces.some(f => f.status === 'loaded') "
                    "|| faces.some(f => f.status === 'error'); }); }",
                    sorted(required),
                )
                if missing:
                    # Pure-CJK text loads no face of its mapped family at all, so the
                    # gate fires before the missing-asset check below. Naming the
                    # unvendored requests here points at the actual cause.
                    print(f"ERROR: required fonts failed to load: {', '.join(missing)}.", file=sys.stderr)
                    if missing_assets:
                        files = ", ".join(sorted(set(missing_assets)))
                        print(f"Unvendored font files were requested: {files}. CJK text is not supported.", file=sys.stderr)
                    print("Aborting so a font-degraded diagram is never produced.", file=sys.stderr)
                    sys.exit(1)

            # The route handlers abort real network requests, so entries here mean an
            # asset was looked up outside vendor/ (for example the bundle's CDN fallback)
            # and the offline guarantee would be silently lost.
            if blocked_requests:
                urls = ", ".join(sorted(set(blocked_requests)))
                print(f"ERROR: render attempted network access: {urls}", file=sys.stderr)
                sys.exit(1)

            # Requests that missed vendor/ mean glyphs outside the vendored fonts, in
            # practice Xiaolai subsets for CJK text. Aborting beats rendering them in
            # whatever font the host OS substitutes (platform-dependent output with
            # exit 0). Emoji and other local-font glyphs never issue a request, so they
            # are outside this guarantee.
            if missing_assets:
                files = ", ".join(sorted(set(missing_assets)))
                print(f"ERROR: diagram needs fonts that are not vendored: {files}", file=sys.stderr)
                print("CJK text is not supported. Aborting so a wrong-font diagram is never produced.", file=sys.stderr)
                sys.exit(1)

            # Screenshot the SVG element
            svg_el = page.query_selector("#root svg")
            if svg_el is None:
                print("ERROR: No SVG element found after render.", file=sys.stderr)
                sys.exit(1)

            svg_el.screenshot(path=str(output_path))

            # The checks above read the route logs at one instant. A request still in
            # flight then has had its handler dispatched during the blocking calls
            # since, and a late hit voids the offline/font guarantees, so the PNG
            # written above must not survive it.
            if blocked_requests or missing_assets:
                output_path.unlink(missing_ok=True)
                stray = ", ".join(sorted(set(blocked_requests) | set(missing_assets)))
                print(f"ERROR: asset or network activity after the render checks: {stray}", file=sys.stderr)
                sys.exit(1)

    return output_path


SELF_CHECK_DIAGRAM = {
    "type": "excalidraw",
    "version": 2,
    "source": "self-check",
    "elements": [
        {
            "id": "c1", "type": "text", "x": 20, "y": 20, "width": 120, "height": 36,
            "angle": 0, "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1,
            "opacity": 100, "groupIds": [], "frameId": None, "roundness": None, "seed": 1,
            "version": 1, "versionNonce": 1, "isDeleted": False, "boundElements": [],
            "updated": 1, "link": None, "locked": False, "text": "ok", "fontSize": 28,
            "fontFamily": 5, "textAlign": "left", "verticalAlign": "top",
            "containerId": None, "originalText": "ok", "lineHeight": 1.25, "baseline": 25,
        },
        {
            "id": "c2", "type": "text", "x": 20, "y": 80, "width": 160, "height": 30,
            "angle": 0, "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1,
            "opacity": 100, "groupIds": [], "frameId": None, "roundness": None, "seed": 2,
            "version": 1, "versionNonce": 2, "isDeleted": False, "boundElements": [],
            "updated": 1, "link": None, "locked": False, "text": "ok", "fontSize": 20,
            "fontFamily": 3, "textAlign": "left", "verticalAlign": "top",
            "containerId": None, "originalText": "ok", "lineHeight": 1.25, "baseline": 16,
        },
    ],
    "appState": {"viewBackgroundColor": "#ffffff"},
    "files": {},
}


def run_self_check() -> None:
    """Render a built-in text fixture to verify the whole pipeline (browser, vendored
    bundles, fonts, screenshot). Exits nonzero on any failure; the font gate inside
    render() makes a passing run imply the hand-drawn font actually loaded."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "self_check.excalidraw"
        src.write_text(json.dumps(SELF_CHECK_DIAGRAM), encoding="utf-8")
        out = Path(d) / "self_check.png"
        render(src, out)
        if not out.exists() or out.stat().st_size == 0:
            print("ERROR: self-check produced no output.", file=sys.stderr)
            sys.exit(1)
    print("OK: render pipeline healthy (browser, vendored bundles, fonts, screenshot).")


def _positive_int(value: str) -> int:
    """Argparse type for --scale/--width. Chromium treats a device scale factor of 0 as
    'no override' and silently renders at the wrong resolution, so non-positive values
    must be rejected up front instead of passed through."""
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Excalidraw JSON to PNG")
    parser.add_argument("input", type=Path, nargs="?", help="Path to .excalidraw JSON file")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output PNG path (default: same name with .png)")
    parser.add_argument("--scale", "-s", type=_positive_int, default=DEFAULT_SCALE, help=f"Device scale factor (default: {DEFAULT_SCALE})")
    parser.add_argument("--width", "-w", type=_positive_int, default=DEFAULT_MAX_WIDTH, help=f"Max viewport width (default: {DEFAULT_MAX_WIDTH})")
    parser.add_argument("--check", action="store_true", help="Render a built-in fixture to verify the pipeline, then exit")
    args = parser.parse_args()

    if args.check:
        if args.input is not None:
            parser.error("--check takes no input file")
        run_self_check()
        return

    if args.input is None:
        parser.error("input is required (or pass --check)")

    if not args.input.exists():
        print(f"ERROR: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    png_path = render(args.input, args.output, args.scale, args.width)
    print(str(png_path))


if __name__ == "__main__":
    main()
