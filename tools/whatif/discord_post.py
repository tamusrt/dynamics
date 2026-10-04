"""Post the JARVIS message, with the VISION picture, to a Discord channel through a webhook.

    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \
        python tools/whatif/discord_post.py --note jarvis_note.json --picture site/predictions/vision.png

The message is an embed: its title links to VISION, the text is jarvis_note.json's, and the picture
is attached (so it shows in Discord even before the site is live). Yellow when something needs
attention, green otherwise. The webhook address comes from the environment, never the command line.
Exits 0 without posting when DISCORD_WEBHOOK_URL is not set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

GREEN, YELLOW = 0x2EA043, 0xD29922


def build(note: dict, picture: Path | None) -> tuple[bytes, str]:
    """The multipart body and its content type."""
    embed = {
        "title": note["title"],
        "url": note["url"],
        "description": note["description"][:4000],
        "color": YELLOW if note.get("warnings") else GREEN,
    }
    if picture is not None:
        embed["image"] = {"url": f"attachment://{picture.name}"}
    payload = {"username": "JARVIS", "embeds": [embed], "allowed_mentions": {"parse": []}}
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
        f"Content-Type: application/json\r\n\r\n".encode() + json.dumps(payload).encode() + b"\r\n"
    ]
    if picture is not None:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{picture.name}"\r\n'
            f"Content-Type: image/png\r\n\r\n".encode() + picture.read_bytes() + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--note", type=Path, default=Path("jarvis_note.json"))
    p.add_argument("--picture", type=Path, default=None)
    a = p.parse_args(argv)
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print("DISCORD_WEBHOOK_URL is not set; nothing posted to Discord.", flush=True)
        return 0
    picture = a.picture if a.picture is not None and a.picture.is_file() else None
    body, kind = build(json.loads(a.note.read_text(encoding="utf-8")), picture)
    request = urllib.request.Request(url, data=body, headers={"Content-Type": kind, "User-Agent": "jarvis-note"})
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:  # noqa: S310  (the team's own webhook)
            print(f"Posted to Discord ({reply.status}).", flush=True)
    except urllib.error.HTTPError as exc:
        print(f"Discord answered {exc.code}: {exc.read().decode(errors='replace')[:300]}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
