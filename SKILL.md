---
name: weiloo-image
description: 使用 Weiloo AI 图片 API 生成图片，或分析 Weiloo 图片生成失败。
---

# Weiloo Image

用户可以显式输入 `$weiloo-image` 调用此 Skill，例如：

```text
$weiloo-image 生成一张赛博朋克城市
```

也支持自然语言触发。

触发场景：

- 生成图片
- 创建图片
- 海报设计
- 图片编辑
- 分析 Weiloo 图片生成失败

执行本 Skill 目录中的 `scripts/imagegen.py` 生成图片。命令的工作目录保持为用户当前
任务目录，不要切换到已安装的 Skill 目录：

```bash
python "<Skill 目录>/scripts/imagegen.py" --prompt "<用户图片描述>"
```

普通生成时直接在前台执行一次脚本。默认图片保存到当前任务目录下的 `outputs/`。
成功后读取并报告脚本输出的 `IMAGE_PATH=` 行；其值是 JSON 字符串。不要通过扫描目录
推断图片路径。不要先读取源码、调用 `/models`、修改已安装 Skill 文件、启动后台进程
或轮询 Python 进程。

图片请求可能计费。生成失败时报告脚本的用户友好错误并停止；不要自动重试。只有用户
明确同意后，才可以再次提交图片生成请求。成功后直接报告文件路径；除非用户要求，
不要额外检查图片画面。

失败时，脚本会输出 `DIAGNOSTIC_REPORT=` 行。读取其 JSON 路径并将 Markdown 报告交付
给用户，不要扫描目录或重复提交图片请求。

只有用户明确询问“为什么生成失败”或显式输入 `$weiloo-image 为什么生成失败` 时，才在
当前任务目录运行：

```bash
python "<Skill 目录>/scripts/imagegen.py" --diagnose
```

该模式不会调用 `/images/generations` 或重试图片生成；它会检查当前本地配置并可读取
`/models`，然后输出新的 `DIAGNOSTIC_REPORT=` 行。报告不应包含或回显 API Key、图片描述、
完整服务响应或完整下载地址。

默认使用 `https://ai.weiloo.com/v1` 和 `gpt-image-2.5`。

首次使用没有 API Key 时，提示：

```text
未检测到 API Key。
请输入 API Key：
```

用户输入后自动保存到 `~/.codex/weiloo-image/config.json`，之后不再要求输入。
收到 API Key 后，运行 `scripts/setup.py` 保存并继续原图片请求，不要回显 API Key。
不要显示 API Key、Python traceback 或原始服务错误。
