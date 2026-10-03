"""Bundle the UI into one HTML file for engine serving and attachment previews.

Runtime has no Node dependency. Rebuild after changing web sources with:
python scripts/build-workspace.py
"""
import argparse
import base64
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "web"


def build(esbuild: str | None = None) -> None:
    command = [esbuild] if esbuild else ["npx", "--yes", "esbuild@0.25.12"]
    with tempfile.TemporaryDirectory(prefix="revector-web-") as temp:
        bundle = Path(temp) / "workspace.js"
        subprocess.run(command + [str(WEB / "workspace.js"), "--bundle", "--format=iife",
                                  "--target=es2020", f"--outfile={bundle}"], check=True)
        markup = subprocess.run(["node", str(ROOT / "scripts" / "prerender-workspace.cjs"),
                                 str(bundle)], check=True, capture_output=True, text=True).stdout
        javascript = bundle.read_text().replace("</script", "<\\/script")
    css = (WEB / "workspace.css").read_text()
    css = css.replace('@import url("./assets/fonts.css");', (WEB / "assets" / "fonts.css").read_text())
    template = (WEB / "workspace.template.html").read_text()
    favicon = base64.b64encode((WEB / "assets" / "mark.svg").read_bytes()).decode()
    html = template.replace('href="./assets/mark.svg"', f'href="data:image/svg+xml;base64,{favicon}"')
    html = html.replace("<!-- WORKSPACE_STYLE -->", f"<style>\n{css}\n</style>")
    html = html.replace('<div id="app"><div class="boot">Loading ReVector workspace…</div></div>',
                        f'<div id="app">{markup}</div>')
    # At the end of body, all workspace elements exist before JS executes.
    html = html.replace("<!-- WORKSPACE_SCRIPT -->", "")
    html = html.replace("</body>", f"<script>\n{javascript}\n</script>\n</body>")
    html = "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
    (WEB / "index.html").write_text(html)
    print(f"Built {WEB / 'index.html'} ({len(html.encode()):,} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--esbuild", help="Optional path to an installed esbuild executable")
    build(parser.parse_args().esbuild)
