#!/usr/bin/env python3
"""
Download YouTube videos as MP3 files into your Downloads folder.

Requirements:
    pip install yt-dlp
    ffmpeg must be installed and on your PATH
        - macOS:   brew install ffmpeg
        - Windows: winget install ffmpeg   (or download from ffmpeg.org)
        - Linux:   sudo apt install ffmpeg

Usage:
    python yt_to_mp3.py "https://www.youtube.com/watch?v=XXXXXXXX"
    python yt_to_mp3.py "URL1" "URL2" "URL3"
"""

import sys
import os
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("Missing dependency. Install it with:\n    pip install yt-dlp")
    sys.exit(1)


def get_downloads_folder() -> Path:
    """Return the user's Downloads folder in a cross-platform way."""
    home = Path.home()
    downloads = home / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    return downloads


def download_as_mp3(urls, output_dir: Path, bitrate: str = "192"):
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": bitrate,
            }
        ],
        "noplaylist": True,   # set to False if you want playlist support
        "quiet": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url in urls:
            print(f"\nDownloading: {url}")
            try:
                ydl.download([url])
            except Exception as e:
                print(f"Failed to download {url}: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python yt_to_mp3.py <youtube_url> [more urls...]")
        sys.exit(1)

    urls = sys.argv[1:]
    output_dir = get_downloads_folder()
    print(f"Saving MP3s to: {output_dir}")
    download_as_mp3(urls, output_dir)
    print("\nDone.")


if __name__ == "__main__":
    main()
