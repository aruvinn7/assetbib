"""Bangun ulang ikon Font Awesome versi ringkas (self-host, hanya ikon yang dipakai).

Font Awesome penuh = CSS 100 KB + font solid 157 KB. Aplikasi hanya memakai
±85 ikon, jadi skrip ini memindai templat/JS/Python, lalu membuat:
  static/vendor/fontawesome/icons.css
  static/vendor/fontawesome/fa-solid-900.woff2   (subset)
  static/vendor/fontawesome/fa-regular-400.woff2 (subset)

Jalankan lagi setiap kali menambah ikon baru (kalau tidak, ikon baru tampil kosong):
    python scripts/build_icons.py
Butuh: pip install fonttools brotli. Sumber Font Awesome diunduh dari cdnjs,
atau pakai --src DIR berisi all.min.css, fa-solid-900.woff2, fa-regular-400.woff2.
"""
import argparse
import pathlib
import re
import sys
import tempfile
import urllib.request

FA_VERSION = "6.5.0"
CDN = f"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{FA_VERSION}"
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "vendor" / "fontawesome"
SCAN = [
    (ROOT / "bibasset", ("*.html", "*.py")),
    (ROOT / "static" / "js", ("*.js",)),
]
# Kelas fa-* yang bukan nama ikon
NOT_ICONS = {"fa-solid", "fa-regular", "fa-brands", "fa-fw"}


def used_icons():
    names = set()
    for base, patterns in SCAN:
        for pat in patterns:
            for f in base.rglob(pat):
                names.update(re.findall(r"\bfa-[a-z0-9]+(?:-[a-z0-9]+)*", f.read_text(encoding="utf-8", errors="ignore")))
    return sorted(names - NOT_ICONS)


def fetch_sources(src):
    if src:
        return pathlib.Path(src)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="fa-src-"))
    for rel in ("css/all.min.css", "webfonts/fa-solid-900.woff2", "webfonts/fa-regular-400.woff2"):
        print("unduh", rel)
        urllib.request.urlretrieve(f"{CDN}/{rel}", tmp / pathlib.Path(rel).name)
    return tmp


def codepoints(css):
    """Peta nama kelas → kode glyph dari aturan `.fa-x:before,.fa-y:before{content:"\\f0f3"}`."""
    table = {}
    for selectors, code in re.findall(r'([^{}]+)\{content:"\\([0-9a-f]+)"\}', css):
        for name in re.findall(r"\.(fa-[a-z0-9-]+):before", selectors):
            table[name] = code
    return table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="folder berisi all.min.css dan file woff2 Font Awesome")
    args = ap.parse_args()

    from fontTools import subset  # noqa: import di sini supaya pesan error jelas bila belum terpasang

    src = fetch_sources(args.src)
    table = codepoints((src / "all.min.css").read_text(encoding="utf-8"))
    names = used_icons()
    missing = [n for n in names if n not in table]
    icons = {n: table[n] for n in names if n in table}
    if missing:
        print("PERINGATAN: bukan nama ikon Font Awesome (diabaikan):", ", ".join(missing))

    OUT.mkdir(parents=True, exist_ok=True)
    unicodes = ",".join(sorted({f"U+{c}" for c in icons.values()}))
    for font in ("fa-solid-900.woff2", "fa-regular-400.woff2"):
        subset.main([
            str(src / font), f"--unicodes={unicodes}", "--flavor=woff2",
            "--layout-features=", "--no-hinting", "--desubroutinize",
            f"--output-file={OUT / font}",
        ])

    rules = "\n".join(f'.{n}:before{{content:"\\{c}"}}' for n, c in sorted(icons.items()))
    css = f"""/*! Font Awesome Free {FA_VERSION} by @fontawesome - https://fontawesome.com
 * License - https://fontawesome.com/license/free (Icons: CC BY 4.0, Fonts: SIL OFL 1.1, Code: MIT License)
 * Copyright 2023 Fonticons, Inc.
 * Subset {len(icons)} ikon — dibuat oleh scripts/build_icons.py, jangan diedit manual. */
@font-face{{font-family:"Font Awesome 6 Free";font-style:normal;font-weight:900;font-display:block;src:url(fa-solid-900.woff2) format("woff2")}}
@font-face{{font-family:"Font Awesome 6 Free";font-style:normal;font-weight:400;font-display:block;src:url(fa-regular-400.woff2) format("woff2")}}
.fa-solid,.fa-regular{{-moz-osx-font-smoothing:grayscale;-webkit-font-smoothing:antialiased;display:var(--fa-display,inline-block);font-style:normal;font-variant:normal;line-height:1;text-rendering:auto;font-family:"Font Awesome 6 Free"}}
.fa-solid{{font-weight:900}}
.fa-regular{{font-weight:400}}
.fa-fw{{text-align:center;width:1.25em}}
{rules}
"""
    (OUT / "icons.css").write_text(css, encoding="utf-8")
    sizes = ", ".join(f"{p.name} {p.stat().st_size / 1024:.1f} KB" for p in sorted(OUT.iterdir()))
    print(f"{len(icons)} ikon -> {sizes}")


if __name__ == "__main__":
    sys.exit(main())
