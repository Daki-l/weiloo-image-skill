#!/usr/bin/env python3
"""Interactive API Key setup for Weiloo Image Skill."""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Final


DEFAULT_BASE_URL: Final = "https://ai.weiloo.com/v1"
DEFAULT_MODEL: Final = "gpt-image-2.5"


class SetupError(Exception):
    """A configuration problem that can be shown directly to a user."""


def default_config_path() -> Path:
    """Return the only persistent configuration location used by this skill."""
    return Path.home() / ".codex" / "weiloo-image" / "config.json"


def _set_private_permissions(path: Path, mode: int) -> None:
    """Best-effort permission tightening on platforms that support POSIX modes."""
    if os.name != "nt":
        try:
            path.chmod(mode)
        except OSError:
            pass


def _write_json_atomically(path: Path, data: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SetupError("无法保存配置，请检查用户目录的写入权限。") from exc
    _set_private_permissions(path.parent, 0o700)

    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".config-",
            suffix=".tmp",
            dir=path.parent,
        )
    except OSError as exc:
        raise SetupError("无法保存配置，请检查用户目录的写入权限。") from exc
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _set_private_permissions(temporary_path, 0o600)
        os.replace(temporary_path, path)
        _set_private_permissions(path, 0o600)
    except OSError as exc:
        raise SetupError("无法保存配置，请检查用户目录的写入权限。") from exc
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def save_config(api_key: str, config_path: Path | None = None) -> Path:
    """Save one API key with the fixed Weiloo endpoint and model defaults."""
    normalized_key = api_key.strip()
    if not normalized_key:
        raise SetupError("API Key 不能为空。")

    destination = config_path or default_config_path()
    _write_json_atomically(
        destination,
        {
            "api_key": normalized_key,
            "base_url": DEFAULT_BASE_URL,
            "model": DEFAULT_MODEL,
        },
    )
    return destination


def main() -> int:
    try:
        api_key = getpass.getpass("请输入 API Key：")
    except (EOFError, KeyboardInterrupt):
        print("未收到 API Key。请重新输入后再试。", file=sys.stderr)
        return 1

    try:
        save_config(api_key)
    except SetupError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print("保存成功。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
