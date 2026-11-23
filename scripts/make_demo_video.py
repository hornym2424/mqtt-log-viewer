"""Compose final demo MP4: title cards + recorded browser demo (~30s)."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "docs" / "demo"
FRAMES = DEMO / "frames"
BROWSER_VIDEO = DEMO / "browser_demo.webm"
FINAL = DEMO / "mqtt-log-viewer-demo.mp4"

FFMPEG = Path(
    r"C:\Users\horny\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
)
FONT = Path(r"C:\Windows\Fonts\meiryo.ttc")
W, H = 1280, 800
BG = (243, 245, 247)
ACCENT = (15, 110, 106)
INK = (28, 36, 48)
MUTED = (92, 107, 122)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # meiryo.ttc index 0=regular, 1=bold roughly; use same file
    return ImageFont.truetype(str(FONT), size=size, index=0)


def card(path: Path, title: str, subtitle: str = "", step: str = "") -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    # top accent bar
    draw.rectangle((0, 0, W, 8), fill=ACCENT)
    if step:
        draw.text((64, 120), step, fill=ACCENT, font=font(28))
    draw.text((64, 180), title, fill=INK, font=font(52))
    y = 280
    for line in textwrap.wrap(subtitle, width=28):
        draw.text((64, y), line, fill=MUTED, font=font(32))
        y += 48
    # footer brand
    draw.text((64, H - 80), "MQTT Log Viewer", fill=ACCENT, font=font(24))
    img.save(path, "PNG")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    if not BROWSER_VIDEO.exists():
        raise SystemExit(f"missing {BROWSER_VIDEO}; run record_demo.py first")
    if not FFMPEG.exists():
        raise SystemExit(f"ffmpeg not found: {FFMPEG}")

    intro = FRAMES / "01_intro.png"
    start = FRAMES / "02_startbat.png"
    outro = FRAMES / "03_outro.png"
    card(intro, "MQTT Log Viewer", "周期抜け検出からCSV出力までの操作デモ", "30秒デモ")
    card(
        start,
        "start.bat をダブルクリック",
        "初回は環境セットアップのあと、ブラウザが自動で開きます",
        "STEP 1",
    )
    card(outro, "完了", "周期抜けを絞り込み、前後区間をCSV / グラフ用CSVへ出力", "")

    # Measure browser video duration
    probe = subprocess.check_output(
        [
            str(FFMPEG).replace("ffmpeg.exe", "ffprobe.exe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(BROWSER_VIDEO),
        ],
        text=True,
    ).strip()
    browser_dur = float(probe)
    print(f"browser video duration: {browser_dur:.2f}s")

    # Target ~30s total: intro 3s + start 3s + browser (scaled) + outro 3s
    intro_d, start_d, outro_d = 3.0, 3.5, 3.0
    target_browser = max(16.0, 30.0 - intro_d - start_d - outro_d)
    speed = browser_dur / target_browser if browser_dur > target_browser else 1.0
    print(f"browser speed factor: {speed:.3f}")

    intro_mp4 = FRAMES / "intro.mp4"
    start_mp4 = FRAMES / "start.mp4"
    outro_mp4 = FRAMES / "outro.mp4"
    browser_mp4 = FRAMES / "browser.mp4"

    for src, dst, dur in (
        (intro, intro_mp4, intro_d),
        (start, start_mp4, start_d),
        (outro, outro_mp4, outro_d),
    ):
        run(
            [
                str(FFMPEG),
                "-y",
                "-loop",
                "1",
                "-i",
                str(src),
                "-t",
                str(dur),
                "-vf",
                f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
                "-r",
                "30",
                "-pix_fmt",
                "yuv420p",
                str(dst),
            ]
        )

    # Caption bars as PNG overlays (avoids Windows escaping issues with drawtext + Japanese)
    caps = []
    for i, (label, color) in enumerate(
        [
            ("STEP 2  周期通信サンプルを開く", ACCENT),
            ("STEP 3  周期抜け ○ 件 で絞り込み", (154, 52, 18)),
            ("STEP 4  前後数秒で切り出し → CSV / グラフ用CSV", ACCENT),
        ],
        start=1,
    ):
        bar = Image.new("RGBA", (W, 64), (*color, 230))
        d = ImageDraw.Draw(bar)
        d.text((24, 14), label, fill=(255, 255, 255, 255), font=font(28))
        path = FRAMES / f"cap{i}.png"
        bar.save(path)
        caps.append(path)

    # Times are on the sped-up browser timeline
    run(
        [
            str(FFMPEG),
            "-y",
            "-i",
            str(BROWSER_VIDEO),
            "-i",
            str(caps[0]),
            "-i",
            str(caps[1]),
            "-i",
            str(caps[2]),
            "-filter_complex",
            (
                f"[0:v]setpts=PTS/{speed},"
                f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2[base];"
                f"[base][1:v]overlay=0:18:enable='between(t,0,3.5)'[v1];"
                f"[v1][2:v]overlay=0:18:enable='between(t,3.5,8.5)'[v2];"
                f"[v2][3:v]overlay=0:18:enable='gte(t,8.5)'[vout]"
            ),
            "-map",
            "[vout]",
            "-an",
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            str(browser_mp4),
        ]
    )

    concat_list = FRAMES / "concat.txt"
    concat_list.write_text(
        "\n".join(
            [
                f"file '{intro_mp4.as_posix()}'",
                f"file '{start_mp4.as_posix()}'",
                f"file '{browser_mp4.as_posix()}'",
                f"file '{outro_mp4.as_posix()}'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    run(
        [
            str(FFMPEG),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(FINAL),
        ]
    )
    size_mb = FINAL.stat().st_size / (1024 * 1024)
    dur = subprocess.check_output(
        [
            str(FFMPEG).replace("ffmpeg.exe", "ffprobe.exe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(FINAL),
        ],
        text=True,
    ).strip()
    print(f"wrote {FINAL} ({size_mb:.1f} MB, {float(dur):.1f}s)")


if __name__ == "__main__":
    main()
