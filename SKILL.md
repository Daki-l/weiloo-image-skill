---
name: weiloo-image
description: 使用 Weiloo AI 图片 API 生成图片。
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

执行 `scripts/imagegen.py` 生成图片：

```bash
python scripts/imagegen.py --prompt "<用户图片描述>"
```

默认使用 `https://ai.weiloo.com/v1` 和 `gpt-image-2.5`。

首次使用没有 API Key 时，提示：

```text
未检测到 API Key。
请输入 API Key：
```

用户输入后自动保存到 `~/.codex/weiloo-image/config.json`，之后不再要求输入。
收到 API Key 后，运行 `scripts/setup.py` 保存并继续原图片请求，不要回显 API Key。
不要显示 API Key、Python traceback 或原始服务错误。
