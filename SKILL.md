---
name: weiloo-image
description: 使用 Weiloo AI 图片 API 生成或编辑图片，并分析 Weiloo 图片生成或编辑失败。
---

# Weiloo Image

当用户要生成图片、创建图片、设计海报、换背景、保留主体改风格，或根据一张或多张附件修改图片时使用本 Skill。

显式调用方式：

```text
Codex：$weiloo-image 一只宇航员猫
WorkBuddy（包括 workbuddy、workBuddy、WORKBUDDY）：/weiloo-image 一只宇航员猫
```

在用户当前任务目录执行本 Skill 内的脚本，不要切换到已安装 Skill 目录：

```bash
python "<Skill 目录>/scripts/imagegen.py" --prompt "<用户图片描述>"
```

普通文生图不要额外探测 `/models` 或 `/images/edits`。脚本会把结果写到当前任务的 `outputs/`，成功后只读取并报告脚本输出的 `IMAGE_PATH=` JSON 路径；不要扫描目录推测输出文件。

## 图片编辑

只有宿主已明确提供可读取的本地附件绝对路径时，才可根据原图编辑。使用每张附件对应的 `--image` 参数，保持宿主给出的顺序：第一张默认是主体图，后续图片默认只作参考。用户指定不同角色时，把角色说明写进 prompt，但不要改变上传顺序。

```bash
python "<Skill 目录>/scripts/imagegen.py" --prompt "第一张图保留主体；第二张图只参考背景；第三张图只参考材质。" --image "<主体图本地路径>" --image "<背景参考图本地路径>" --image "<材质参考图本地路径>"
```

支持一到四张 PNG、JPEG 或 WebP 本地图片。不要扫描下载目录、临时目录或工作目录寻找附件，不要猜测路径，也不要接受远程 URL、mask 或批量编辑。当前任务没有明确可读路径时，如实说明无法以用户原图编辑；不要重新生成相似图片后声称已完成编辑。

一次生成或编辑只在前台提交一次请求。失败时报告脚本的用户友好错误并停止，不要自动重试。失败输出中的 `DIAGNOSTIC_REPORT=` 是 JSON 路径；读取该 Markdown 报告交付给用户，不要扫描目录或再次提交请求。

仅当用户明确询问“为什么生成失败”或显式调用失败分析时，运行：

```bash
python "<Skill 目录>/scripts/imagegen.py" --diagnose
```

诊断不会调用图片生成或图片编辑接口，也不会重试先前请求；它可检查本地配置和 `/models`。不要在对话、命令行输出或报告中回显 API Key、图片描述、完整输入路径、完整服务响应或完整下载地址。

首次使用没有 API Key 时，脚本会提示输入并保存到 `~/.codex/weiloo-image/config.json`。收到 API Key 后运行内置配置流程并继续原请求，不要回显密钥、显示 Python traceback 或原始服务错误。
