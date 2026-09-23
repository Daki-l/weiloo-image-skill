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
import time
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
DIAGNOSTIC_TIMEOUT_SECONDS: Final = 30
MAX_JSON_RESPONSE_BYTES: Final = 5 * 1024 * 1024
MAX_IMAGE_BYTES: Final = 50 * 1024 * 1024
SIZE_PATTERN: Final = re.compile(r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$")
PHASE_LABELS: Final = {
    "configuration": "配置读取",
    "input": "输入校验",
    "generation_request": "图片生成请求",
    "generation_response": "图片生成响应",
    "image_download": "图片下载",
    "local_write": "本地写入",
    "unknown": "未知阶段",
}


class ImagegenError(Exception):
    """An expected failure that is safe to show to a non-technical user."""

    def __init__(
        self,
        message: str,
        *,
        phase: str = "unknown",
        status_code: int | None = None,
        response_mode: str | None = None,
        remote_host: str | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.status_code = status_code
        self.response_mode = response_mode
        self.remote_host = remote_host


class ConfigurationRequiredError(ImagegenError):
    """Raised when the saved API Key is absent or empty."""


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str


@dataclass(frozen=True)
class ModelProbe:
    """A redacted result from the optional, non-image diagnostic request."""

    endpoint_status: str
    model_status: str


class FriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ImagegenError(f"参数不正确：{message}", phase="input")


def default_config_path() -> Path:
    return Path.home() / ".codex" / "weiloo-image" / "config.json"


def _normalize_base_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImagegenError("配置中的 API 地址无效，请重新配置。", phase="configuration")
    base_url = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImagegenError("配置中的 API 地址无效，请重新配置。", phase="configuration")
    return base_url


def _normalized_string(value: object, default: str, field_name: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ImagegenError(f"配置中的 {field_name} 无效，请重新配置。", phase="configuration")
    return value.strip()


def load_config(config_path: Path | None = None) -> Config:
    """Load the user configuration, falling back only for optional defaults."""
    location = config_path or default_config_path()
    if not location.is_file():
        raise ConfigurationRequiredError("未找到 API Key。", phase="configuration")

    try:
        raw = json.loads(location.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImagegenError("配置文件无法读取，请重新配置。", phase="configuration") from exc

    if not isinstance(raw, dict):
        raise ImagegenError("配置文件格式错误，请重新配置。", phase="configuration")

    api_key = raw.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigurationRequiredError("未找到 API Key。", phase="configuration")

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
        raise ImagegenError("无法启动配置流程，请重新尝试。", phase="configuration") from exc

    if completed.returncode != 0:
        raise ConfigurationRequiredError(
            "没有 API Key。请重新输入 API Key 后重试。",
            phase="configuration",
        )


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
        raise ImagegenError("图片尺寸格式应为 1024x1024。", phase="input")
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


def _read_limited(
    response: Any,
    limit: int,
    error_message: str,
    *,
    phase: str,
    response_mode: str | None = None,
    remote_host: str | None = None,
) -> bytes:
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ImagegenError(
            error_message,
            phase=phase,
            response_mode=response_mode,
            remote_host=remote_host,
        )
    return body


def _read_json_response(response: Any, *, phase: str = "generation_response") -> dict[str, Any]:
    body = _read_limited(
        response,
        MAX_JSON_RESPONSE_BYTES,
        "服务返回内容过大，请稍后重试。",
        phase=phase,
    )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImagegenError("服务返回的数据无效，请稍后重试。", phase=phase) from exc
    if not isinstance(payload, dict):
        raise ImagegenError("服务返回的数据无效，请稍后重试。", phase=phase)
    return payload


def _decode_base64_image(value: str) -> bytes:
    encoded = value.strip()
    if encoded.startswith("data:"):
        metadata, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in metadata.lower():
            raise ImagegenError(
                "服务返回的图片数据无效，请稍后重试。",
                phase="generation_response",
                response_mode="b64_json",
            )
    try:
        image = base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImagegenError(
            "服务返回的图片数据无效，请稍后重试。",
            phase="generation_response",
            response_mode="b64_json",
        ) from exc
    if not image or len(image) > MAX_IMAGE_BYTES:
        raise ImagegenError(
            "服务返回的图片数据无效，请稍后重试。",
            phase="generation_response",
            response_mode="b64_json",
        )
    return image


def _download_image(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImagegenError(
            "服务返回的图片地址无效，请稍后重试。",
            phase="generation_response",
            response_mode="url",
        )
    remote_host = parsed.hostname
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "image/*",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            image = _read_limited(
                response,
                MAX_IMAGE_BYTES,
                "图片文件过大，请稍后重试。",
                phase="image_download",
                response_mode="url",
                remote_host=remote_host,
            )
    except urllib.error.HTTPError as exc:
        raise ImagegenError(
            friendly_error(exc.code),
            phase="image_download",
            status_code=exc.code,
            response_mode="url",
            remote_host=remote_host,
        ) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
        raise ImagegenError(
            "图片下载失败，请检查网络连接后重试。",
            phase="image_download",
            response_mode="url",
            remote_host=remote_host,
        ) from exc
    if not image:
        raise ImagegenError(
            "服务返回的图片数据无效，请稍后重试。",
            phase="image_download",
            response_mode="url",
            remote_host=remote_host,
        )
    return image


def image_bytes_from_response(payload: dict[str, Any]) -> bytes:
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ImagegenError("服务没有返回图片，请稍后重试。", phase="generation_response")

    item = data[0]
    base64_image = item.get("b64_json")
    if isinstance(base64_image, str) and base64_image.strip():
        return _decode_base64_image(base64_image)

    image_url = item.get("url")
    if isinstance(image_url, str) and image_url.strip():
        return _download_image(image_url.strip())

    raise ImagegenError("服务没有返回图片，请稍后重试。", phase="generation_response")


def _write_new_file_atomically(output_path: Path, image: bytes) -> None:
    """Publish an image without overwriting an existing user artifact."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ImagegenError("无法创建图片目录，请检查写入权限。", phase="local_write") from exc

    if output_path.exists():
        raise ImagegenError("目标文件已存在，请指定新的 --output 路径。", phase="local_write")

    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".weiloo-image-",
            suffix=".tmp",
            dir=output_path.parent,
        )
    except OSError as exc:
        raise ImagegenError("无法保存图片，请检查输出目录是否可写。", phase="local_write") from exc
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(image)
            handle.flush()
            os.fsync(handle.fileno())

        try:
            os.link(temporary_path, output_path)
        except FileExistsError as exc:
            raise ImagegenError(
                "目标文件已存在，请指定新的 --output 路径。",
                phase="local_write",
            ) from exc
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
                raise ImagegenError(
                    "目标文件已存在，请指定新的 --output 路径。",
                    phase="local_write",
                ) from exc
            except OSError as exc:
                if created:
                    try:
                        output_path.unlink()
                    except OSError:
                        pass
                raise ImagegenError(
                    "无法保存图片，请检查输出目录是否可写。",
                    phase="local_write",
                ) from exc
    except OSError as exc:
        raise ImagegenError("无法保存图片，请检查输出目录是否可写。", phase="local_write") from exc
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def generate_image(config: Config, prompt: str, size: str, output_path: Path) -> Path:
    """Request one image and publish the first returned item to ``output_path``."""
    request = build_generation_request(config, prompt, size)
    try:
        response = urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        raise ImagegenError(
            friendly_error(exc.code),
            phase="generation_request",
            status_code=exc.code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
        raise ImagegenError(friendly_error(None), phase="generation_request") from exc

    try:
        with response:
            response_payload = _read_json_response(response)
    except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
        raise ImagegenError(
            "图片服务响应读取失败，请检查网络连接后重试。",
            phase="generation_response",
        ) from exc

    image = image_bytes_from_response(response_payload)
    _write_new_file_atomically(output_path, image)
    return output_path


def _slugify(value: str) -> str:
    result = "".join(
        character if character.isascii() and character.isalnum() else "-"
        for character in value.lower()
    )
    result = re.sub(r"-+", "-", result).strip("-")
    return result[:36] or "image"


def default_output_path(prompt: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(3)
    filename = f"weiloo-{timestamp}-{_slugify(prompt)}-{suffix}.png"
    return Path.cwd() / "outputs" / filename


def _artifact_path_line(label: str, path: Path) -> str:
    """Emit an ASCII-only path record that survives terminal encoding changes."""
    return f"{label}={json.dumps(str(path.resolve()), ensure_ascii=True)}"


def _diagnostic_report_path(kind: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(3)
    return Path.cwd() / "outputs" / f"weiloo-image-{kind}-{timestamp}-{suffix}.md"


def _write_diagnostic_report(path: Path, content: str) -> Path | None:
    """Write a unique report without masking the image operation's original error."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except OSError:
        return None
    return path.resolve()


def _safe_host(value: str | None) -> str:
    if not isinstance(value, str):
        return "未获取"
    normalized = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9.-]+", normalized):
        return "未获取"
    return normalized or "未获取"


def _failure_report_markdown(error: ImagegenError, elapsed_ms: int) -> str:
    phase = PHASE_LABELS.get(error.phase, PHASE_LABELS["unknown"])
    status_code = str(error.status_code) if error.status_code is not None else "未获取"
    response_mode = error.response_mode if error.response_mode in {"b64_json", "url"} else "未获取"
    return "\n".join(
        [
            "# Weiloo 图片生成诊断报告",
            "",
            "本次图片没有写入当前任务。以下内容来自本地脚本当次执行，不能证明服务端是否生成了图片。",
            "",
            "## 当次证据",
            f"- 失败阶段：{phase}",
            "- 用户提示：请查看当前 Codex 对话中的失败消息",
            f"- 总耗时：{elapsed_ms} ms",
            f"- HTTP 状态：{status_code}",
            f"- 响应方式：{response_mode}",
            f"- 下载主机：{_safe_host(error.remote_host)}",
            "",
            "## 下一步",
            "请让当前助手分析本次图片生成失败原因，可运行当前环境的只读诊断。该诊断不会调用图片生成接口，也不会重试本次请求。",
            "",
            "## 隐私",
            "本报告不记录 API Key、图片描述、完整服务响应、完整下载地址或环境变量。",
            "",
        ]
    )


def write_failure_report(error: ImagegenError, elapsed_ms: int) -> Path | None:
    return _write_diagnostic_report(
        _diagnostic_report_path("failure"),
        _failure_report_markdown(error, elapsed_ms),
    )


def _probe_models(config: Config) -> ModelProbe:
    request = urllib.request.Request(
        f"{config.base_url}/models",
        method="GET",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=DIAGNOSTIC_TIMEOUT_SECONDS) as response:
            payload = _read_json_response(response, phase="diagnostic_probe")
            status_code = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        return ModelProbe(f"HTTP {exc.code}", "未检查")
    except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError):
        return ModelProbe("网络失败", "未检查")
    except ImagegenError:
        return ModelProbe("响应无效", "未检查")

    data = payload.get("data")
    if not isinstance(data, list):
        return ModelProbe(f"HTTP {status_code}", "未检查")
    model_ids = {
        item.get("id")
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    return ModelProbe(
        f"HTTP {status_code}",
        "可用" if config.model in model_ids else "未列出",
    )


def run_environment_diagnostic() -> Path:
    """Create a report from a real local check without making an image request."""
    try:
        config = load_config()
    except ConfigurationRequiredError:
        configuration_status = "未检测到 API Key"
        probe = ModelProbe("未调用", "未检查")
    except ImagegenError:
        configuration_status = "配置无效或无法读取"
        probe = ModelProbe("未调用", "未检查")
    else:
        configuration_status = "已读取配置"
        probe = _probe_models(config)

    report = "\n".join(
        [
            "# Weiloo 图片生成环境诊断报告",
            "",
            "本报告反映生成失败后执行诊断时的本地环境，不会重试此前的图片请求。",
            "",
            "## 本次检查",
            "- 图片生成接口：未调用",
            f"- 配置：{configuration_status}",
            f"- 模型接口：{probe.endpoint_status}",
            f"- 当前模型：{probe.model_status}",
            "- 输出目录：报告已写入当前任务的 `outputs/` 目录",
            "",
            "## 隐私",
            "本报告不记录 API Key、图片描述、完整服务响应、完整下载地址或环境变量。",
            "",
        ]
    )
    report_path = _write_diagnostic_report(_diagnostic_report_path("diagnostic"), report)
    if report_path is None:
        raise ImagegenError("无法保存诊断报告，请检查输出目录是否可写。", phase="local_write")
    return report_path


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
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="生成当前环境的图片失败诊断报告，不会生成图片",
    )
    return parser


def _resolve_prompt(args: argparse.Namespace) -> str:
    if args.prompt and args.prompt_option:
        raise ImagegenError("请只提供一次图片描述。", phase="input")
    prompt = args.prompt_option or args.prompt
    if not isinstance(prompt, str) or not prompt.strip():
        raise ImagegenError("请提供图片描述，例如：生成一张未来城市图片。", phase="input")
    return prompt.strip()


def main(argv: list[str] | None = None) -> int:
    started_at = time.monotonic()
    try:
        args = build_parser().parse_args(argv)
        if args.configure and args.diagnose:
            raise ImagegenError("配置与诊断不能同时运行。", phase="input")
        if args.configure:
            _run_setup()
            return 0
        if args.diagnose:
            if args.prompt or args.prompt_option or args.output:
                raise ImagegenError("诊断模式不接受图片描述或输出路径。", phase="input")
            report_path = run_environment_diagnostic()
            print(_artifact_path_line("DIAGNOSTIC_REPORT", report_path))
            return 0

        prompt = _resolve_prompt(args)
        size = validate_size(args.size)
        output_path = Path(args.output).expanduser() if args.output else default_output_path(prompt)
        generated = generate_image(ensure_config(), prompt, size, output_path)
    except ImagegenError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        report_path = write_failure_report(exc, int((time.monotonic() - started_at) * 1000))
        if report_path is not None:
            print(_artifact_path_line("DIAGNOSTIC_REPORT", report_path))
        return 1
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130

    # Keep the success record ASCII-only so Codex can recover it through
    # terminals whose display encoding does not support Chinese text.
    print(_artifact_path_line("IMAGE_PATH", generated))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
