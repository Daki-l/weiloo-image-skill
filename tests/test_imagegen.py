from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeJsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, _size: int | None = None) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False


class FakeBinaryResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, _size: int | None = None) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False


class ImagegenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.imagegen = load_module("weiloo_imagegen_under_test", "imagegen.py")
        self.setup = load_module("weiloo_setup_under_test", "setup.py")

    def test_setup_writes_the_supported_user_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "weiloo-image" / "config.json"

            self.setup.save_config("test-api-key", config_path)

            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8")),
                {
                    "api_key": "test-api-key",
                    "base_url": "https://ai.weiloo.com/v1",
                    "model": "gpt-image-2.5",
                },
            )

    def test_config_uses_defaults_when_optional_values_are_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{"api_key":"test-api-key"}', encoding="utf-8")

            config = self.imagegen.load_config(config_path)

            self.assertEqual(config.base_url, "https://ai.weiloo.com/v1")
            self.assertEqual(config.model, "gpt-image-2.5")

    def test_missing_key_starts_the_setup_flow(self) -> None:
        configured = self.imagegen.Config(
            api_key="test-api-key",
            base_url="https://ai.weiloo.com/v1",
            model="gpt-image-2.5",
        )

        with (
            mock.patch.object(
                self.imagegen,
                "load_config",
                side_effect=[self.imagegen.ConfigurationRequiredError("未找到 API Key。"), configured],
            ),
            mock.patch.object(self.imagegen, "_run_setup") as run_setup,
        ):
            actual = self.imagegen.ensure_config()

        self.assertEqual(actual, configured)
        run_setup.assert_called_once_with(first_use=True)

    def test_user_configuration_overrides_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "api_key": "test-api-key",
                        "base_url": "https://images.example.test/v1",
                        "model": "custom-image-model",
                    }
                ),
                encoding="utf-8",
            )

            config = self.imagegen.load_config(config_path)

            self.assertEqual(config.api_key, "test-api-key")
            self.assertEqual(config.base_url, "https://images.example.test/v1")
            self.assertEqual(config.model, "custom-image-model")

    def test_generate_posts_the_expected_payload_and_saves_base64_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "future-city.png"
            config = self.imagegen.Config(
                api_key="test-api-key",
                base_url="https://ai.weiloo.com/v1",
                model="gpt-image-2.5",
            )
            image_bytes = b"\x89PNG\r\n\x1a\nimage-data"
            encoded_image = base64.b64encode(image_bytes).decode("ascii")
            response = FakeJsonResponse({"data": [{"b64_json": encoded_image}]})

            with mock.patch.object(self.imagegen.urllib.request, "urlopen", return_value=response) as urlopen:
                written_path = self.imagegen.generate_image(
                    config,
                    prompt="一座未来城市",
                    size="1024x1024",
                    output_path=output_path,
                )

            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, "https://ai.weiloo.com/v1/images/generations")
            self.assertEqual(
                json.loads(request.data.decode("utf-8")),
                {
                    "model": "gpt-image-2.5",
                    "prompt": "一座未来城市",
                    "size": "1024x1024",
                },
            )
            self.assertEqual(request.get_header("Authorization"), "Bearer test-api-key")
            self.assertEqual(request.get_header("Content-type"), "application/json")
            self.assertEqual(
                request.get_header("User-agent"),
                "Codex-Weiloo-Image-Skill/1.0",
            )
            self.assertEqual(written_path, output_path)
            self.assertEqual(output_path.read_bytes(), image_bytes)

    def test_generate_downloads_url_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "future-city.png"
            config = self.imagegen.Config(
                api_key="test-api-key",
                base_url="https://ai.weiloo.com/v1",
                model="gpt-image-2.5",
            )
            image_bytes = b"url-image-data"
            responses = [
                FakeJsonResponse({"data": [{"url": "https://images.example.test/result.png"}]}),
                FakeBinaryResponse(image_bytes),
            ]

            with mock.patch.object(self.imagegen.urllib.request, "urlopen", side_effect=responses) as urlopen:
                self.imagegen.generate_image(
                    config,
                    prompt="一座未来城市",
                    size="1024x1024",
                    output_path=output_path,
                )

            self.assertEqual(urlopen.call_count, 2)
            download_request = urlopen.call_args_list[1].args[0]
            self.assertEqual(download_request.get_header("Accept"), "image/*")
            self.assertEqual(
                download_request.get_header("User-agent"),
                "Codex-Weiloo-Image-Skill/1.0",
            )
            self.assertEqual(output_path.read_bytes(), image_bytes)

    def test_save_failure_is_reported_without_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "future-city.png"

            with mock.patch.object(self.imagegen.tempfile, "mkstemp", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(self.imagegen.ImagegenError, "无法保存图片"):
                    self.imagegen._write_new_file_atomically(output_path, b"image-data")

    def test_http_and_network_failures_have_simple_chinese_messages(self) -> None:
        self.assertEqual(self.imagegen.friendly_error(401), "API Key 无效，请检查。")
        self.assertEqual(
            self.imagegen.friendly_error(403),
            "图片服务拒绝该请求，请检查账户权限或稍后重试。",
        )
        self.assertEqual(self.imagegen.friendly_error(429), "API 请求次数达到限制。")
        self.assertEqual(self.imagegen.friendly_error(None), "无法连接图片服务，请检查网络。")

    def test_url_download_uses_the_same_friendly_http_error(self) -> None:
        error = urllib.error.HTTPError(
            "https://images.example.test/result.png",
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"error":"do not show this"}'),
        )

        with mock.patch.object(self.imagegen.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(self.imagegen.ImagegenError, "图片服务拒绝") as ctx:
                self.imagegen._download_image("https://images.example.test/result.png")

        self.assertNotIn("do not show this", str(ctx.exception))

    def test_http_error_is_mapped_without_exposing_the_server_body(self) -> None:
        config = self.imagegen.Config(
            api_key="test-api-key",
            base_url="https://ai.weiloo.com/v1",
            model="gpt-image-2.5",
        )
        error = urllib.error.HTTPError(
            "https://ai.weiloo.com/v1/images/generations",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":"do not show this"}'),
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            mock.patch.object(self.imagegen.urllib.request, "urlopen", side_effect=error),
        ):
            with self.assertRaisesRegex(self.imagegen.ImagegenError, "API Key 无效") as ctx:
                self.imagegen.generate_image(
                    config,
                    prompt="一张图片",
                    size="1024x1024",
                    output_path=Path(temp_dir) / "image.png",
                )

        self.assertNotIn("do not show this", str(ctx.exception))

    def test_default_output_path_uses_an_ascii_filename_in_workspace_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            with mock.patch.object(self.imagegen.Path, "cwd", return_value=workspace):
                output_path = self.imagegen.default_output_path("一张未来城市图片")

        self.assertEqual(output_path.parent, workspace / "outputs")
        self.assertTrue(output_path.name.isascii())
        self.assertTrue(output_path.name.endswith(".png"))

    def test_main_emits_a_machine_readable_success_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "outputs" / "future-city-城市.png"
            generated_path = output_path.resolve()
            stdout = io.StringIO()

            with (
                mock.patch.object(self.imagegen, "ensure_config"),
                mock.patch.object(self.imagegen, "generate_image", return_value=generated_path),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = self.imagegen.main(
                    ["--prompt", "一座未来城市", "--output", str(output_path)]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            stdout.getvalue(),
            f"IMAGE_PATH={json.dumps(str(generated_path), ensure_ascii=True)}\n",
        )
        self.assertTrue(stdout.getvalue().isascii())


if __name__ == "__main__":
    unittest.main()
