"""Fixed Playwright runner for model-authored HTML; no model Python execution."""
import base64
from html.parser import HTMLParser
import os
from pathlib import Path
import shutil
import signal
import subprocess


class StaticHTML(HTMLParser):
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "iframe", "object", "embed", "base"}:
            raise ValueError(f"Generated HTML contains unsupported {tag} element")
        if tag == "meta" and dict(attrs).get("http-equiv", "").lower() == "refresh":
            raise ValueError("Generated HTML must not redirect")


def validate_html(text):
    if "<html" not in text.lower() or "<body" not in text.lower():
        raise ValueError("Expected a complete HTML document")
    parser = StaticHTML()
    parser.feed(text)
    parser.close()


def renderer_environment():
    bundle = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
    node = os.environ.get("STUDIO_PDF_NODE") or shutil.which("node")
    if not node and (bundle / "bin/node").exists():
        node = str(bundle / "bin/node")
    if not node:
        raise RuntimeError("Node.js is required; set STUDIO_PDF_NODE to its executable")
    # The browser process does not inherit API keys from the calling process.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "TEMP", "SYSTEMROOT") if key in os.environ}
    modules = os.environ.get("NODE_PATH")
    if not modules and (bundle / "node_modules/playwright").exists():
        modules = str(bundle / "node_modules")
    if modules:
        env["NODE_PATH"] = modules
    if os.environ.get("CHROME_PATH"):
        env["CHROME_PATH"] = os.environ["CHROME_PATH"]
    return node, env


def require_renderer():
    node, env = renderer_environment()
    check = subprocess.run([node, "-e", "require.resolve('playwright')"], env=env,
                           capture_output=True, timeout=10)
    if check.returncode:
        raise RuntimeError("Playwright is required; install it or set NODE_PATH to its node_modules directory")
    script = Path(__file__).resolve().parents[2] / "scripts/render_html_pair.cjs"
    try:
        check = subprocess.run([node, str(script), "--check"], env=env,
                               capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Chrome launch preflight timed out; generation was not called") from None
    if check.returncode:
        raise RuntimeError("Chrome launch preflight failed; check browser installation and launch permissions. Generation was not called")


def font_css(paths):
    faces = []
    for bold, path in paths.items():
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        faces.append("@font-face{font-family:Doc;font-style:normal;font-weight:"+
                     ("700" if bold else "400")+";src:url(data:font/ttf;base64,"+
                     encoded+") format('truetype');}")
    return "\n".join(faces) + "\nbody{font-family:Doc,sans-serif;}"


def render_pair(documents, directory, fonts, language, timeout=120):
    for name, text, lang in [("source", documents.source_html, "en"),
                             ("reference", documents.reference_html, language)]:
        validate_html(text)
        # Preserve model-authored HTML verbatim. The fixed renderer adds font assets only.
        (directory / f"{name}.html").write_text(text)
        (directory / f"{name}-fonts.css").write_text(font_css(fonts[lang]))
    node, env = renderer_environment()
    script = Path(__file__).resolve().parents[2] / "scripts/render_html_pair.cjs"
    with (directory / "render.log").open("wb") as log:
        process = subprocess.Popen([node, str(script), str(directory.resolve())], env=env,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            status = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise RuntimeError("HTML rendering stopped; see render.log") from None
    if status:
        raise RuntimeError(f"HTML renderer exited with status {status}; see render.log")
    for name in ("source.pdf", "reference.pdf"):
        if not (directory / name).is_file():
            raise RuntimeError(f"HTML renderer did not create {name}")
