import sys, re, time, subprocess, urllib.request, urllib.parse, zipfile, shutil
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
FFMPEG_DIR   = SCRIPT_DIR / "ffmpeg_bin"
LOCAL_FFMPEG = FFMPEG_DIR / "ffmpeg.exe"

FFMPEG_ZIP_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":          "https://www.xvideos.com",
    "Referer":         "https://www.xvideos.com/",
}

def ffmpeg_version(exe: str) -> tuple[int, ...]:
    try:
        out = subprocess.check_output(
            [exe, "-version"], stderr=subprocess.STDOUT, timeout=10
        ).decode(errors="replace")
        m = re.search(r"ffmpeg version[^\d]*(\d+)\.(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return (0, 0)


def find_good_ffmpeg() -> str | None:
    if LOCAL_FFMPEG.exists() and ffmpeg_version(str(LOCAL_FFMPEG)) >= (4, 0):
        return str(LOCAL_FFMPEG)

    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg and ffmpeg_version(sys_ffmpeg) >= (4, 0):
        return sys_ffmpeg

    return None


def download_ffmpeg() -> str:
    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = FFMPEG_DIR / "ffmpeg.zip"

    print("  Downloading modern ffmpeg …")
    def reporthook(block, bsize, total):
        done = block * bsize
        pct  = done / total * 100 if total > 0 else 0
        bar  = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
        print(f"\r  [{bar}] {pct:4.0f}%", end="", flush=True)

    urllib.request.urlretrieve(FFMPEG_ZIP_URL, zip_path, reporthook)

    print("  Extracting …")
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if re.search(r"/bin/ffmpeg\.exe$", member):
                zf.extract(member, FFMPEG_DIR)
                extracted = FFMPEG_DIR / member
                if LOCAL_FFMPEG.exists():
                    LOCAL_FFMPEG.unlink()
                extracted.replace(LOCAL_FFMPEG)
                break
    zip_path.unlink(missing_ok=True)
    for item in FFMPEG_DIR.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)

    print(f"  ffmpeg saved to: {LOCAL_FFMPEG}")
    return str(LOCAL_FFMPEG)


def ensure_ffmpeg() -> str:
    ff = find_good_ffmpeg()
    if ff:
        return ff
    print("\n  ffmpeg is missing or too old.")
    print("  downloading ffmpeg …")
    return download_ffmpeg()

def fetch(url: str, binary: bool = False):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read() if binary else r.read().decode("utf-8", errors="replace")

def resolve(base: str, url: str) -> str:
    return urllib.parse.urljoin(base, url)

def parse_m3u8(url: str) -> list:
    text = fetch(url)
    if "#EXT-X-STREAM-INF" in text:
        best_bw, best_uri = -1, None
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                m  = re.search(r"BANDWIDTH=(\d+)", line)
                bw = int(m.group(1)) if m else 0
                nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if nxt and not nxt.startswith("#") and bw > best_bw:
                    best_bw, best_uri = bw, nxt
        if not best_uri:
            raise ValueError("No variant streams found.")
        return parse_m3u8(resolve(url, best_uri))

    base = url.rsplit("/", 1)[0] + "/"
    segs = [resolve(base, l.strip()) for l in text.splitlines()
            if l.strip() and not l.startswith("#")]
    if not segs:
        raise ValueError("No segments found.")
    return segs

def download_segments(segments: list, out_path: str):
    total = len(segments)
    with open(out_path, "wb") as f:
        for idx, seg in enumerate(segments, 1):
            for attempt in range(1, 4):
                try:
                    f.write(fetch(seg, binary=True))
                    pct = idx / total * 100
                    bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
                    print(f"\r  [{bar}] {pct:5.1f}%  seg {idx}/{total}",
                          end="", flush=True)
                    break
                except Exception as e:
                    if attempt == 3:
                        print(f"\n  WARNING: seg {idx} failed: {e}")
                    else:
                        time.sleep(1.5 * attempt)
    print()

def remux_to_h264(src: str, ffmpeg_exe: str) -> str:
    """Re-encode src to H.264/AAC and return the new filename."""
    p        = Path(src)
    out_path = str(p.parent / (p.stem + "_h264.mp4"))
    print(f"\n  Converting AV1 → H.264 (this may take a few minutes) …")
    print(f"  Output: {out_path}")
    cmd = [
        ffmpeg_exe, "-y",
        "-i",  src,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac",     "-b:a",    "192k",
        "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd)
    if result.returncode == 0 and Path(out_path).stat().st_size > 1_000:
        print("  Conversion successful.")
        return out_path
    else:
        print("  Conversion failed — returning original file.")
        return src

def derive_filename(url: str) -> str:
    decoded  = urllib.parse.unquote(url)
    filename = decoded.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    m = re.search(r"(\d{3,4}p)", filename) or re.search(r"(\d{3,4}p)", decoded)
    quality  = m.group(1) if m else "video"
    return f"{quality}_output.mp4"

def try_ytdlp(url: str, out_path: str, ffmpeg_exe: str) -> bool:
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

    print("  yt-dlp found — using it …\n")
    p = Path(out_path)
    cmd = [
        "yt-dlp",
        "--add-header", "Referer:https://www.xvideos.com/",
        "--add-header", "Origin:https://www.xvideos.com",
        "--no-update",
        "--ffmpeg-location", ffmpeg_exe,
        "-o", out_path,
        url,
    ]
    subprocess.run(cmd)

    if p.exists() and p.stat().st_size > 1_000:
        return True
    for candidate in Path(".").glob(f"{p.stem}.*"):
        if candidate.stat().st_size > 1_000:
            candidate.rename(out_path)
            return True
    return False

def main():
    if len(sys.argv) < 2:
        print("Usage: python downloader.py <m3u8_url> [output.mp4]")
        sys.exit(1)

    url      = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) >= 3 else derive_filename(url)
    is_av1   = "av1" in url.lower()

    print(f"\n{'='*60}")
    print(f"  Source  : {url[:80]}…")
    print(f"  Output  : {out_path}")
    print(f"  Codec   : {'AV1 (will convert to H.264)' if is_av1 else 'auto'}")
    print(f"{'='*60}\n")

    print("[Step 1] Checking ffmpeg …")
    ffmpeg_exe = ensure_ffmpeg()
    ver = ffmpeg_version(ffmpeg_exe)
    print(f"  Using ffmpeg {ver[0]}.{ver[1]} at: {ffmpeg_exe}\n")

    downloaded = False

    print("[Step 2] Downloading video …")
    if try_ytdlp(url, out_path, ffmpeg_exe):
        downloaded = True
    else:
        print("  yt-dlp unavailable — using built-in HLS downloader …")
        try:
            segs = parse_m3u8(url)
            print(f"  {len(segs)} segments  →  {out_path}")
            download_segments(segs, out_path)
            downloaded = True
        except Exception as e:
            print(f"  Download failed: {e}")
            sys.exit(1)

    if not downloaded:
        print("  All download methods failed.")
        sys.exit(1)

    size_mb = Path(out_path).stat().st_size / 1_048_576
    print(f"  Downloaded: {out_path}  ({size_mb:.1f} MB)")

    if is_av1:
        print("\n[Step 3] Converting AV1 → H.264 for universal playback …")
        final = remux_to_h264(out_path, ffmpeg_exe)
    else:
        final = out_path

    final_mb = Path(final).stat().st_size / 1_048_576
    print(f"\n{'='*60}")
    print(f"  ✓  Done!  Final file : {final}  ({final_mb:.1f} MB)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
