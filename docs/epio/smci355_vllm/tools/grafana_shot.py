#!/usr/bin/env python3
"""grafana_shot.py: a screenshot of part of our Grafana dashboard, for the docs.

  /var/tmp/yutongwu_epio/shotvenv/bin/python tools/grafana_shot.py '<dashboard URL path and query>' out.png \
      --first '<title the shot starts at>' --last '<title of the last panel in it>'

Headless Chromium (playwright in /var/tmp/yutongwu_epio/shotvenv, browsers in /var/tmp/yutongwu_epio/pw-browsers) on
the login node against http://127.0.0.1:23300, logged in with monitor/grafana-conf/admin_password. The shot runs from
the element whose text starts with --first to the bottom of the panel whose title starts with --last.
"""
import argparse
import os

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/var/tmp/yutongwu_epio/pw-browsers")
from playwright.sync_api import sync_playwright  # noqa: E402

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="e.g. /d/epio?var-experiment=...&from=...&to=...")
    ap.add_argument("out")
    ap.add_argument("--first", required=True)
    ap.add_argument("--last", required=True)
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--wait", type=float, default=8, help="s for the panels to load")
    a = ap.parse_args()
    pw = open(os.path.join(W, "monitor", "grafana-conf", "admin_password")).read().strip()
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": a.width, "height": 4000}, http_credentials={"username": "admin", "password": pw},
                            timezone_id="Asia/Shanghai")
        page = ctx.new_page()
        page.goto("http://127.0.0.1:23300" + a.path + ("&" if "?" in a.path else "?") + "kiosk=true",
                  wait_until="networkidle")
        page.wait_for_timeout(a.wait * 1000)
        first = page.get_by_text(a.first, exact=False).first
        last = page.locator("section[data-testid*='Panel header'], [data-testid='data-testid panel content']").filter(
            has_text=a.last).first
        if not last.count():
            last = page.get_by_text(a.last, exact=False).first.locator("xpath=ancestor::section[1]")
        first.scroll_into_view_if_needed()
        page.wait_for_timeout(2000)
        f, l_ = first.bounding_box(), last.bounding_box()
        page.screenshot(path=a.out, clip={"x": 0, "y": f["y"] - 8, "width": a.width,
                                          "height": l_["y"] + l_["height"] - f["y"] + 16})
        b.close()
    print(a.out)


if __name__ == "__main__":
    main()
