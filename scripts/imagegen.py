#!/usr/bin/env python3
"""Generate one image through Weiloo's OpenAI-compatible Images API."""

from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
from typing import Any, Final
import urllib.error
import urllib.parse
import urllib.request


SCRIPT_DIR: Final = Path(__file__).resolve().parent
DEFAULT_BASE_URL: Final = "https://ai.weiloo.com/v1"
DEFAULT_MODEL: Final = "gpt-image-2.5"
DEFAULT_SIZE: Final = "1024x1024"
USER_AGENT: Final = "Codex-Weiloo-Image-Skill/1.0"
REQUEST_TIMEOUT_SECONDS: Final = 120
MAX_JSON_RESPONSE_BYTES: Final = 5 * 1024 * 1024
MAX_IMAGE_BYTES: Final = 50 * 1024 * 1024
SIZE_PATTERN: Final = re.compile(r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$")


class ImagegenError(Exception):
    """An expected failure that is safe to show to a non-technical user."""


class ConfigurationRequiredError(ImagegenError):
    """Raised when the saved API Key is absent or empty."""


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str


class FriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ImagegenError(f"参数不正确：{message}")


def default_config_path() -> Path:
    return Path.home() / ".codex" / "weiloo-image" / "config.json"


def _normalize_base_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImagegenError("配置中的 API 地址无效，请重新配置。")
    base_url = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImagegenError("配置中的 API 地址无效，请重新配置。")
    return base_url


def _normalized_string(value: object, default: str, field_name: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ImagegenError(f"配置中的 {field_name} 无效，请重新配置。")
    return value.strip()


def load_config(config_path: Path | None = None) -> Config:
    """Load the user configuration, falling back only for optional defaults."""
    location = config_path or default_config_path()
    if not location.is_file():
        raise ConfigurationRequiredError("未找到 API Key。")

    try:
        raw = json.loads(location.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImagegenError("配置文件无法读取，请重新配置。") from exc

    if not isinstance(raw, dict):
        raise ImagegenError("配置文件格式错误，请重新配置。")

    api_key = raw.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigurationRequiredError("未找到 API Key。")

    return Config(
        api_key=api_key.strip(),
        base_url=_normalize_base_url(raw.get("base_url", DEFAULT_BASE_URL)),
        model=_normalized_string(raw.get("model"), DEFAULT_MODEL, "模型"),
    )


def _run_setup(first_use: bool = False) -> None:
    """Invoke the bundled setup flow without putting a secret on the command line."""
    if first_use:
        print("未检测到 API Key。")
    setup_script = SCRIPT_DIR / "setup.py"
    try:
        completed = subprocess.run([sys.executable, str(setup_script)], check=False)
    except OSError as exc:
        raise ImagegenError("无法启动配置流程，请重新尝试。") from exc

    if completed.returncode != 0:
        raise ConfigurationRequiredError("没有 API Key。请在 Codex 中输入 API Key 后重试。")


def ensure_config() -> Config:
    """Load saved credentials or launch first-use setup automatically."""
    try:
        return load_config()
    except ConfigurationRequiredError:
        _run_setup(first_use=True)
        return load_config()


def friendly_error(status_code: int | None) -> str:
    """Map transport failures to the short messages promised to users."""
    if status_code == 401:
        return "API Key 无效，请检查。"
    if status_code == 403:
        return "图片服务拒绝该请求，请检查账户权限或稍后重试。"
    if status_code == 429:
        return "API 请求次数达到限制。"
    if status_code is None:
        return "无法连接图片服务，请检查网络。"
    return f"图片生成失败（服务返回 {status_code}）。请稍后再试。"


def validate_size(value: str) -> str:
    size = value.strip()
    if not SIZE_PATTERN.fullmatch(size):
        raise ImagegenError("图片尺寸格式应为 1024x1024。")
    return size


def build_generation_request(config: Config, prompt: str, size: str) -> urllib.request.Request:
    payload = {
        "model": config.model,
        "prompt": prompt,
        "size": size,
    }
    return urllib.request.Request(
        f"{config.base_url}/images/generations",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )


def _read_limited(response: Any, limit: int, error_message: str) -> bytes:
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ImagegenError(error_message)
    return body


def _read_json_response(response: Any) -> dict[str, Any]:
    body = _read_limited(response, MAX_JSON_RESPONSE_BYTES, "服务返回内容过大，请稍后重试。")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImagegenError("服务返回的数据无效，请稍后重试。") from exc
    if not isinstance(payload, dict):
        raise ImagegenError("服务返回的数据无效，请稍后重试。")
    return payload


def _decode_base64_image(value: str) -> bytes:
    encoded = value.strip()
    if encoded.startswith("data:"):
        metadata, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in metadata.lower():
            raise ImagegenError("服务返回的图片数据无效，请稍后重试。")
    try:
        image = base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImagegenError("服务返回的图片数据无效，请稍后重试。") from exc
    if not image or len(image) > MAX_IMAGE_BYTES:
        raise ImagegenError("服务返回的图片数据无效，请稍后重试。")
    return image


def _download_image(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImagegenError("服务返回的图片地址无效，请稍后重试。")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "image/*",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            image = _read_limited(response, MAX_IMAGE_BYTES, "图片文件过大，请稍后重试。")
    except urllib.error.HTTPError as exc:
        raise ImagegenError(friendly_error(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
        raise ImagegenError("图片下载失败，请检查网络连接后重试。") from exc
    if not image:
        raise ImagegenError("服务返回的图片数据无效，请稍后重试。")
    return image


def image_bytes_from_response(payload: dict[str, Any]) -> bytes:
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ImagegenError("服务没有返回图片，请稍后重试。")

    item = data[0]
    base64_image = item.get("b64_json")
    if isinstance(base64_image, str) and base64_image.strip():
        return _decode_base64_image(base64_image)

    image_url = item.get("url")
    if isinstance(image_url, str) and image_url.strip():
        return _download_image(image_url.strip())

    raise ImagegenError("服务没有返回图片，请稍后重试。")


def _write_new_file_atomically(output_path: Path, image: bytes) -> None:
    """Publish an image without overwriting an existing user artifact."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ImagegenError("无法创建图片目录，请检查写入权限。") from exc

    if output_path.exists():
        raise ImagegenError("目标文件已存在，请指定新的 --output 路径。")

    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".weiloo-image-",
            suffix=".tmp",
            dir=output_path.parent,
        )
    except OSError as exc:
        raise ImagegenError("无法保存图片，请检查输出目录是否可写。") from exc
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(image)
            handle.flush()
            os.fsync(handle.fileno())

        try:
            os.link(temporary_path, output_path)
        except FileExistsError as exc:
            raise ImagegenError("目标文件已存在，请指定新的 --output 路径。") from exc
        except OSError:
            # Some filesystems do not support hard links. The exclusive create
            # fallback still preserves the no-overwrite contract.
            created = False
            try:
                with output_path.open("xb") as handle:
                    created = True
                    handle.write(image)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError as exc:
                raise ImagegenError("目标文件已存在，请指定新的 --output 路径。") from exc
            except OSError as exc:
                if created:
                    try:
                        output_path.unlink()
                    except OSError:
                        pass
                raise ImagegenError("无法保存图片，请检查输出目录是否可写。") from exc
    except OSError as exc:
        raise ImagegenError("无法保存图片，请检查输出目录是否可写。") from exc
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def generate_image(config: Config, prompt: str, size: str, output_path: Path) -> Path:
    """Request one image and publish the first returned item to ``output_path``."""
    request = build_generation_request(config, prompt, size)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response_payload = _read_json_response(response)
    except urllib.error.HTTPError as exc:
        raise ImagegenError(friendly_error(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
        raise ImagegenError(friendly_error(None)) from exc

    image = image_bytes_from_response(response_payload)
    _write_new_file_atomically(output_path, image)
    return output_path


def _slugify(value: str) -> str:
    result = "".join(character if character.isalnum() else "-" for character in value.lower())
    result = re.sub(r"-+", "-", result).strip("-")
    return result[:36] or "image"


def default_output_path(prompt: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(3)
    filename = f"weiloo-{timestamp}-{_slugify(prompt)}-{suffix}.png"
    return Path.cwd() / "generated" / filename


def build_parser() -> argparse.ArgumentParser:
    parser = FriendlyArgumentParser(description="使用 Weiloo AI 生成一张图片。")
    parser.add_argument("prompt", nargs="?", help="图片描述")
    parser.add_argument("-p", "--prompt", dest="prompt_option", help="图片描述")
    parser.add_argument("--size", default=DEFAULT_SIZE, help="图片尺寸，默认 1024x1024")
    parser.add_argument("-o", "--output", help="图片保存路径")
    parser.add_argument(
        "--configure",
        action="store_true",
        help="重新输入并保存 API Key",
    )
    return parser


def _resolve_prompt(args: argparse.Namespace) -> str:
    if args.prompt and args.prompt_option:
        raise ImagegenError("请只提供一次图片描述。")
    prompt = args.prompt_option or args.prompt
    if not isinstance(prompt, str) or not prompt.strip():
        raise ImagegenError("请提供图片描述，例如：生成一张未来城市图片。")
    return prompt.strip()


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.configure:
            _run_setup()
            return 0

        prompt = _resolve_prompt(args)
        size = validate_size(args.size)
        output_path = Path(args.output).expanduser() if args.output else default_output_path(prompt)
        generated = generate_image(ensure_config(), prompt, size, output_path)
    except ImagegenError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130

    print(f"图片已生成：{generated.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
