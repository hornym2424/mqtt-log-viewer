"""Record a ~30s browser demo with a visible cursor overlay."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "demo"
RAW_DIR = OUT_DIR / "raw"
URL = "http://127.0.0.1:8000/?demo=record"

CURSOR_JS = r"""
(() => {
  if (window.__demoCursor) return;
  const cursor = document.createElement("div");
  cursor.id = "demo-cursor";
  cursor.innerHTML = `
    <svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 2 L4 18 L9 14 L12 21 L14.5 20 L11.5 13 L18 13 Z"
            fill="#111827" stroke="#ffffff" stroke-width="1.4"
            stroke-linejoin="round"/>
    </svg>`;
  Object.assign(cursor.style, {
    position: "fixed",
    left: "0px",
    top: "0px",
    width: "28px",
    height: "28px",
    zIndex: "2147483647",
    pointerEvents: "none",
    transform: "translate(-2px, -2px)",
    transition: "transform 40ms linear",
    filter: "drop-shadow(0 1px 2px rgba(0,0,0,.45))",
  });
  const ripple = document.createElement("div");
  Object.assign(ripple.style, {
    position: "fixed",
    width: "18px",
    height: "18px",
    borderRadius: "50%",
    border: "2px solid #0f6e6a",
    background: "rgba(15,110,106,0.2)",
    zIndex: "2147483646",
    pointerEvents: "none",
    opacity: "0",
    transform: "translate(-50%, -50%) scale(0.4)",
  });
  const moveTo = (x, y) => {
    cursor.style.left = `${x}px`;
    cursor.style.top = `${y}px`;
  };
  const clickAt = (x, y) => {
    ripple.style.left = `${x}px`;
    ripple.style.top = `${y}px`;
    ripple.style.transition = "none";
    ripple.style.opacity = "1";
    ripple.style.transform = "translate(-50%, -50%) scale(0.4)";
    requestAnimationFrame(() => {
      ripple.style.transition = "opacity 350ms ease, transform 350ms ease";
      ripple.style.opacity = "0";
      ripple.style.transform = "translate(-50%, -50%) scale(2.2)";
    });
  };
  document.documentElement.append(cursor, ripple);
  document.addEventListener("mousemove", (e) => moveTo(e.clientX, e.clientY), true);
  document.addEventListener("mousedown", (e) => clickAt(e.clientX, e.clientY), true);
  window.__demoCursor = { move: moveTo, click: clickAt };
  // Hide the OS cursor so only the overlay is visible in the recording.
  const style = document.createElement("style");
  style.textContent = "* { cursor: none !important; }";
  document.documentElement.append(style);
})();
"""


def wait(page: Page, seconds: float) -> None:
    page.wait_for_timeout(int(seconds * 1000))


def center_of(page: Page, selector: str) -> tuple[float, float]:
    box = page.locator(selector).first.bounding_box()
    if not box:
        raise RuntimeError(f"no box for {selector}")
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def move_cursor(page: Page, x: float, y: float, steps: int = 28) -> None:
    page.mouse.move(x, y, steps=steps)
    page.evaluate("(p) => window.__demoCursor && window.__demoCursor.move(p.x, p.y)", {"x": x, "y": y})


def click_with_cursor(page: Page, selector: str, steps: int = 28) -> None:
    page.locator(selector).first.scroll_into_view_if_needed()
    x, y = center_of(page, selector)
    move_cursor(page, x, y, steps=steps)
    wait(page, 0.25)
    page.evaluate("(p) => window.__demoCursor && window.__demoCursor.click(p.x, p.y)", {"x": x, "y": y})
    page.mouse.down()
    wait(page, 0.08)
    page.mouse.up()
    wait(page, 0.2)


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for old in RAW_DIR.glob("*.webm"):
        old.unlink()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ja-JP",
            record_video_dir=str(RAW_DIR),
            record_video_size={"width": 1280, "height": 800},
            accept_downloads=True,
        )
        page = context.new_page()
        page.add_init_script(CURSOR_JS)
        page.goto(URL, wait_until="networkidle")
        page.evaluate(CURSOR_JS)
        move_cursor(page, 200, 200, steps=1)
        wait(page, 1.0)

        # 1) Load periodic sample
        page.select_option("#sampleSelect", "sample_periodic.log")
        wait(page, 0.4)
        click_with_cursor(page, "#btnSample")
        page.wait_for_selector("#gapBadge")
        wait(page, 1.6)

        # 2) Filter to gaps only
        click_with_cursor(page, "#gapBadge", steps=36)
        page.wait_for_function("() => document.querySelectorAll('#messageBody tr').length === 3")
        wait(page, 1.8)

        # 3) Select first gap, set ±2s, narrow
        click_with_cursor(page, "#messageBody tr", steps=32)
        wait(page, 0.8)
        click_with_cursor(page, "#windowSec", steps=24)
        page.fill("#windowSec", "2")
        wait(page, 0.4)
        click_with_cursor(page, "#btnWindow", steps=22)
        page.wait_for_function(
            "() => Number(document.getElementById('countLabel').textContent.match(/\\d+/)?.[0] || 0) > 0"
        )
        wait(page, 1.8)

        # 4) Export CSV and graph CSV
        with page.expect_download() as d1:
            click_with_cursor(page, "#btnExportCsv", steps=30)
        d1.value.save_as(str(OUT_DIR / "demo_export.csv"))
        wait(page, 1.0)

        with page.expect_download() as d2:
            click_with_cursor(page, "#btnExportGraph", steps=26)
        d2.value.save_as(str(OUT_DIR / "demo_graph.csv"))
        wait(page, 1.6)

        page.close()
        context.close()
        browser.close()

    videos = list(RAW_DIR.glob("*.webm"))
    if not videos:
        raise SystemExit("no video recorded")
    target = OUT_DIR / "browser_demo.webm"
    if target.exists():
        target.unlink()
    videos[0].replace(target)
    print(f"recorded {target}")


if __name__ == "__main__":
    main()
