from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import stat
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


class TimeoutResponse:
    def read(self, _size: int | None = None) -> bytes:
        raise socket.timeout("timed out")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False


class ImagegenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.imagegen = load_module("weiloo_imagegen_under_test", "imagegen.py")
        self.setup = load_module("weiloo_setup_under_test", "setup.py")

    def _config(self):
        return self.imagegen.Config(
            api_key="test-api-key",
            base_url="https://ai.weiloo.com/v1",
            model="gpt-image-2.5",
        )

    def _write_edit_image(self, directory: Path, name: str, image_type: str) -> Path:
        payloads = {
            "png": b"\x89PNG\r\n\x1a\nfixture-png",
            "jpeg": b"\xff\xd8\xfffixture-jpeg",
            "webp": b"RIFF\x0c\x00\x00\x00WEBPfixture-webp",
        }
        path = directory / name
        path.write_bytes(payloads[image_type])
        return path

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

    def test_edit_posts_ordered_multipart_images_without_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = self._write_edit_image(directory, "主体 原图.png", "png")
            background = self._write_edit_image(directory, "背景.jpeg", "jpeg")
            material = self._write_edit_image(directory, "材质.webp", "webp")
            output_path = directory / "edited.png"
            input_images = self.imagegen.load_edit_images(
                [str(source), str(background), str(material)]
            )
            result = b"\x89PNG\r\n\x1a\nedit-result"
            encoded_result = base64.b64encode(result).decode("ascii")
            response = FakeJsonResponse({"data": [{"b64_json": encoded_result}]})

            with mock.patch.object(
                self.imagegen.urllib.request,
                "urlopen",
                return_value=response,
            ) as urlopen:
                written_path = self.imagegen.edit_image(
                    self._config(),
                    prompt="第一张图是主体，第二张图参考背景，第三张图参考材质。",
                    size="1024x1024",
                    images=input_images,
                    output_path=output_path,
                )

            request = urlopen.call_args.args[0]
            content_type = request.get_header("Content-type")
            body = request.data
            self.assertEqual(request.full_url, "https://ai.weiloo.com/v1/images/edits")
            self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
            self.assertEqual(body.count(b'name="image"'), 3)
            self.assertLess(body.index(b'filename="image-1.png"'), body.index(b'filename="image-2.jpg"'))
            self.assertLess(body.index(b'filename="image-2.jpg"'), body.index(b'filename="image-3.webp"'))
            self.assertIn("第一张图是主体".encode("utf-8"), body)
            self.assertNotIn(str(source).encode("utf-8"), body)
            self.assertNotIn(str(background).encode("utf-8"), body)
            self.assertNotIn(str(material).encode("utf-8"), body)
            self.assertEqual(written_path, output_path)
            self.assertEqual(output_path.read_bytes(), result)

    def test_edit_request_preserves_two_image_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            first = self._write_edit_image(directory, "first.webp", "webp")
            second = self._write_edit_image(directory, "second.png", "png")
            images = self.imagegen.load_edit_images([str(first), str(second)])

            request = self.imagegen.build_edit_request(
                self._config(),
                "第一张作为主体，第二张只参考风格。",
                "1024x1024",
                images,
            )

        body = request.data
        self.assertEqual(body.count(b'name="image"'), 2)
        self.assertLess(body.index(b'filename="image-1.webp"'), body.index(b'filename="image-2.png"'))
        self.assertIn(b"RIFF\x0c\x00\x00\x00WEBPfixture-webp", body)
        self.assertIn(b"\x89PNG\r\n\x1a\nfixture-png", body)

    def test_edit_accepts_png_jpeg_and_webp_by_binary_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            png = self._write_edit_image(directory, "not-an-extension.bin", "png")
            jpeg = self._write_edit_image(directory, "not-an-extension.data", "jpeg")
            webp = self._write_edit_image(directory, "not-an-extension.file", "webp")

            images = self.imagegen.load_edit_images([str(png), str(jpeg), str(webp)])

        self.assertEqual(
            [(image.filename, image.content_type) for image in images],
            [
                ("image-1.png", "image/png"),
                ("image-2.jpg", "image/jpeg"),
                ("image-3.webp", "image/webp"),
            ],
        )

    def test_edit_downloads_url_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = self._write_edit_image(directory, "source.png", "png")
            output_path = directory / "edited.png"
            responses = [
                FakeJsonResponse({"data": [{"url": "https://images.example.test/edited.png"}]}),
                FakeBinaryResponse(b"edited-from-url"),
            ]

            with mock.patch.object(
                self.imagegen.urllib.request,
                "urlopen",
                side_effect=responses,
            ) as urlopen:
                self.imagegen.edit_image(
                    self._config(),
                    "换成夜景背景。",
                    "1024x1024",
                    self.imagegen.load_edit_images([str(source)]),
                    output_path,
                )

            self.assertEqual(urlopen.call_count, 2)
            self.assertEqual(urlopen.call_args_list[0].args[0].full_url, "https://ai.weiloo.com/v1/images/edits")
            self.assertEqual(urlopen.call_args_list[1].args[0].get_header("Accept"), "image/*")
            self.assertEqual(output_path.read_bytes(), b"edited-from-url")

    def test_edit_rejects_missing_paths_directories_and_unreadable_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            missing = directory / "missing.png"
            with self.assertRaisesRegex(self.imagegen.ImagegenError, "未找到可读取") as missing_error:
                self.imagegen.load_edit_images([str(missing)])
            self.assertNotIn(str(missing), str(missing_error.exception))

            with self.assertRaisesRegex(self.imagegen.ImagegenError, "普通本地文件"):
                self.imagegen.load_edit_images([str(directory)])

            source = self._write_edit_image(directory, "source.png", "png")
            with mock.patch.object(self.imagegen.os, "access", return_value=False):
                with self.assertRaisesRegex(self.imagegen.ImagegenError, "无法读取"):
                    self.imagegen.load_edit_images([str(source)])

            with mock.patch.object(Path, "open", side_effect=OSError("denied")):
                with self.assertRaisesRegex(self.imagegen.ImagegenError, "无法读取"):
                    self.imagegen.load_edit_images([str(source)])

    def test_edit_rejects_symbolic_links_before_reading_or_uploading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._write_edit_image(Path(temp_dir), "source.png", "png")
            link_status = mock.Mock(st_mode=stat.S_IFLNK)

            with (
                mock.patch.object(Path, "lstat", return_value=link_status),
                mock.patch.object(self.imagegen.os, "access") as access,
                mock.patch.object(Path, "open") as open_file,
            ):
                with self.assertRaisesRegex(self.imagegen.ImagegenError, "普通本地文件"):
                    self.imagegen.load_edit_images([str(source)])

            access.assert_not_called()
            open_file.assert_not_called()

    def test_edit_requires_a_host_provided_absolute_path(self) -> None:
        with self.assertRaisesRegex(self.imagegen.ImagegenError, "图片绝对路径"):
            self.imagegen.load_edit_images(["source.png"])

    def test_edit_rejects_size_limits_and_more_than_four_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = self._write_edit_image(directory, "source.png", "png")
            second = self._write_edit_image(directory, "second.png", "png")

            with mock.patch.object(self.imagegen, "MAX_EDIT_IMAGE_BYTES", 8):
                with self.assertRaisesRegex(self.imagegen.ImagegenError, "单张输入图片"):
                    self.imagegen.load_edit_images([str(source)])

            with (
                mock.patch.object(self.imagegen, "MAX_EDIT_IMAGE_BYTES", 20),
                mock.patch.object(self.imagegen, "MAX_EDIT_TOTAL_BYTES", 17),
            ):
                with self.assertRaisesRegex(self.imagegen.ImagegenError, "总大小"):
                    self.imagegen.load_edit_images([str(source), str(second)])

            with self.assertRaisesRegex(self.imagegen.ImagegenError, "最多可使用 4 张"):
                self.imagegen.load_edit_images([str(source)] * 5)

    def test_edit_rejects_forged_and_unsupported_image_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            forged = directory / "forged.png"
            forged.write_bytes(b"this is not a PNG")
            bitmap = directory / "unsupported.bmp"
            bitmap.write_bytes(b"BMnot-a-supported-format")

            with self.assertRaisesRegex(self.imagegen.ImagegenError, "PNG、JPEG 或 WebP"):
                self.imagegen.load_edit_images([str(forged)])
            with self.assertRaisesRegex(self.imagegen.ImagegenError, "PNG、JPEG 或 WebP"):
                self.imagegen.load_edit_images([str(bitmap)])

    def test_main_uses_edit_mode_only_when_images_are_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = self._write_edit_image(directory, "source.png", "png")
            output_path = directory / "edited.png"
            generated_path = output_path.resolve()
            stdout = io.StringIO()

            with (
                mock.patch.object(self.imagegen, "ensure_config", return_value=self._config()),
                mock.patch.object(self.imagegen, "edit_image", return_value=generated_path) as edit_image,
                mock.patch.object(self.imagegen, "generate_image") as generate_image,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = self.imagegen.main(
                    ["--prompt", "保留主体，替换背景。", "--image", str(source), "--output", str(output_path)]
                )

        self.assertEqual(exit_code, 0)
        edit_image.assert_called_once()
        generate_image.assert_not_called()
        self.assertTrue(stdout.getvalue().startswith("IMAGE_PATH="))

    def test_edit_failure_submits_once_and_writes_a_redacted_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            source = self._write_edit_image(workspace, "private-source.png", "png")
            stdout = io.StringIO()
            stderr = io.StringIO()
            http_error = urllib.error.HTTPError(
                "https://ai.weiloo.com/v1/images/edits",
                429,
                "Too Many Requests",
                {},
                io.BytesIO(b'{"error":"do not show this"}'),
            )

            with (
                mock.patch.object(self.imagegen.Path, "cwd", return_value=workspace),
                mock.patch.object(self.imagegen, "ensure_config", return_value=self._config()),
                mock.patch.object(self.imagegen.urllib.request, "urlopen", side_effect=http_error) as urlopen,
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = self.imagegen.main(
                    ["--prompt", "不要泄露这个提示词。", "--image", str(source)]
                )

            line = stdout.getvalue().strip()
            report_path = Path(json.loads(line.split("=", 1)[1]))
            report = report_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertEqual(urlopen.call_count, 1)
        self.assertIn("图片编辑请求", report)
        self.assertIn("HTTP 状态：429", report)
        self.assertNotIn("不要泄露这个提示词。", report)
        self.assertNotIn(str(source), report)
        self.assertNotIn("test-api-key", report)
        self.assertNotIn("do not show this", stderr.getvalue())

    def test_diagnose_rejects_image_arguments_without_network_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                mock.patch.object(self.imagegen.Path, "cwd", return_value=workspace),
                mock.patch.object(self.imagegen.urllib.request, "urlopen") as urlopen,
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = self.imagegen.main(["--diagnose", "--image", "C:/not-used.png"])

        self.assertEqual(exit_code, 1)
        urlopen.assert_not_called()
        self.assertIn("诊断模式不接受", stderr.getvalue())

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

    def test_failed_generation_writes_a_redacted_diagnostic_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            stdout = io.StringIO()
            stderr = io.StringIO()
            failure = self.imagegen.ImagegenError(
                "图片下载失败，请检查网络连接后重试。 test-api-key",
                phase="image_download",
                status_code=503,
                response_mode="url",
                remote_host="cdn.example.test",
            )

            with (
                mock.patch.object(self.imagegen.Path, "cwd", return_value=workspace),
                mock.patch.object(self.imagegen, "ensure_config"),
                mock.patch.object(self.imagegen, "generate_image", side_effect=failure),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = self.imagegen.main(["--prompt", "一张含有秘密的图片"])

            line = stdout.getvalue().strip()
            self.assertTrue(line.startswith("DIAGNOSTIC_REPORT="))
            report_path = Path(json.loads(line.split("=", 1)[1]))
            report = report_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertIn("图片下载", report)
        self.assertIn("HTTP 状态：503", report)
        self.assertIn("响应方式：url", report)
        self.assertIn("下载主机：cdn.example.test", report)
        self.assertNotIn("一张含有秘密的图片", report)
        self.assertNotIn("test-api-key", report)
        self.assertTrue(line.isascii())

    def test_response_read_timeout_is_tagged_as_generation_response(self) -> None:
        config = self.imagegen.Config(
            api_key="test-api-key",
            base_url="https://ai.weiloo.com/v1",
            model="gpt-image-2.5",
        )

        with mock.patch.object(
            self.imagegen.urllib.request,
            "urlopen",
            return_value=TimeoutResponse(),
        ):
            with self.assertRaises(self.imagegen.ImagegenError) as ctx:
                self.imagegen.generate_image(
                    config,
                    prompt="一张图片",
                    size="1024x1024",
                    output_path=Path("image.png"),
                )

        self.assertEqual(ctx.exception.phase, "generation_response")

    def test_diagnose_checks_models_without_generating_an_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            config = self.imagegen.Config(
                api_key="test-api-key",
                base_url="https://ai.weiloo.com/v1",
                model="gpt-image-2.5",
            )
            stdout = io.StringIO()
            response = FakeJsonResponse({"data": [{"id": "gpt-image-2.5"}]})

            with (
                mock.patch.object(self.imagegen.Path, "cwd", return_value=workspace),
                mock.patch.object(self.imagegen, "load_config", return_value=config),
                mock.patch.object(self.imagegen.urllib.request, "urlopen", return_value=response) as urlopen,
                mock.patch.object(self.imagegen, "generate_image") as generate_image,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = self.imagegen.main(["--diagnose"])

            line = stdout.getvalue().strip()
            self.assertTrue(line.startswith("DIAGNOSTIC_REPORT="))
            report_path = Path(json.loads(line.split("=", 1)[1]))
            report = report_path.read_text(encoding="utf-8")
            request = urlopen.call_args.args[0]

        self.assertEqual(exit_code, 0)
        self.assertEqual(request.full_url, "https://ai.weiloo.com/v1/models")
        self.assertEqual(request.get_method(), "GET")
        generate_image.assert_not_called()
        self.assertIn("图片生成接口：未调用", report)
        self.assertIn("模型接口：HTTP 200", report)
        self.assertIn("当前模型：可用", report)
        self.assertNotIn("test-api-key", report)
        self.assertTrue(line.isascii())


if __name__ == "__main__":
    unittest.main()
