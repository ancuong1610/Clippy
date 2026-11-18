"""Composio YouTube test: connect channel, upload one video, inspect its status.

  uv run python scripts/composio_youtube.py schema            # show the upload tool's inputs
  uv run python scripts/composio_youtube.py connect           # prints a link to approve the channel
  uv run python scripts/composio_youtube.py upload FILE --title "..." [--privacy unlisted]
  uv run python scripts/composio_youtube.py status VIDEO_ID   # is it locked private?

Needs COMPOSIO_API_KEY in .env (never printed).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv

USER_ID = "stream-chaos-clips"


def client():
    load_dotenv()
    if not os.getenv("COMPOSIO_API_KEY"):
        sys.exit("COMPOSIO_API_KEY missing in .env")
    from composio import Composio
    return Composio(api_key=os.environ["COMPOSIO_API_KEY"])


def show(x):
    if hasattr(x, "model_dump"):
        x = x.model_dump()
    print(json.dumps(x, indent=2, default=str)[:4000])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["schema", "connect", "upload", "status", "tools"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--title", default="Stream Chaos Clips test upload")
    ap.add_argument("--privacy", default="unlisted", choices=["unlisted", "private", "public"])
    a = ap.parse_args()
    c = client()

    if a.cmd == "tools":
        for t in c.tools.get_raw_composio_tools(toolkits=["YOUTUBE"]):
            print(t.slug)
    elif a.cmd == "schema":
        t = c.tools.get_raw_composio_tool_by_slug(a.arg or "YOUTUBE_UPLOAD_VIDEO")
        show(t.input_parameters)
    elif a.cmd == "connect":
        cfgs = [x for x in c.auth_configs.list(toolkit_slug="youtube").items]
        if cfgs:
            cfg_id = cfgs[0].id
        else:  # Composio-managed OAuth app for YouTube
            cfg_id = c.auth_configs.create("youtube", {"type": "use_composio_managed_auth"}).id
        print("auth config:", cfg_id)
        req = c.connected_accounts.link(user_id=USER_ID, auth_config_id=cfg_id)
        print("Open this link and pick the Stream Chaos Clips channel:\n", req.redirect_url)
        acc = req.wait_for_connection(timeout=900)
        print("connected:", getattr(acc, "id", acc), getattr(acc, "status", ""))
    elif a.cmd == "upload":
        from composio.core.models._files import FileUploadable
        video = os.path.abspath(a.arg)
        # NOTE: YOUTUBE_UPLOAD_VIDEO produced "Processing abandoned" twice (2026-11-18);
        # YOUTUBE_MULTIPART_UPLOAD_VIDEO processed fine and stayed unlisted (not locked).
        fu = FileUploadable.from_path(c.client, video, tool="YOUTUBE_MULTIPART_UPLOAD_VIDEO", toolkit="youtube",
                                      file_upload_allowlist=[__import__("pathlib").Path(video).parent])
        args = {"title": a.title, "description": "Stream Chaos Clips", "tags": ["GTA6"],
                "categoryId": "20", "privacyStatus": a.privacy, "videoFile": fu.model_dump()}
        show(c.tools.execute("YOUTUBE_MULTIPART_UPLOAD_VIDEO", args, user_id=USER_ID,
                             dangerously_skip_version_check=True))
    elif a.cmd == "status":
        show(c.tools.execute("YOUTUBE_GET_VIDEO_DETAILS_BATCH",
                             {"id": [a.arg], "parts": ["status", "snippet"]}, user_id=USER_ID,
                             dangerously_skip_version_check=True))


if __name__ == "__main__":
    main()
