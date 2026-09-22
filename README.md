# Weiloo Image Skill

## 安装

在 Codex 中复制并发送：

```text
安装这个 Skill：
https://github.com/Daki-l/weiloo-image-skill
```

## 配置

第一次使用时输入 API Key，Codex 会自动保存。也可以运行 `python scripts/setup.py` 输入 API Key。

## 使用

在 Codex 中输入：

```text
$weiloo-image 一只宇航员猫
```

即可生成图片。生成后的图片会保存在当前任务的 `outputs` 文件夹中。

## 图片失败

生成失败时，Codex 会给出一份诊断报告文件。输入：

```text
$weiloo-image 为什么生成失败
```

可以检查当前环境并生成新的报告。该检查不会重新生成图片。
