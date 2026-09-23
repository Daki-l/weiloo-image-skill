# Weiloo Image Skill

## 安装

在 Codex 中复制并发送：

```text
安装这个 Skill：
https://github.com/weilooAi/weiloo-image-skill
```

在 WorkBuddy、workbuddy、workBuddy 或 WORKBUDDY 中复制并发送：

```text
在 WorkBuddy 安装这个 Skill：
https://github.com/weilooAi/weiloo-image-skill
```

## 配置

首次使用会提示输入 Weiloo API Key。输入一次后会自动保存，之后不需要修改配置文件、设置环境变量或使用命令行。

也可以让助手运行 Skill 的配置流程。不要把真实 API Key 直接写在安装消息、命令历史或 Git 仓库里。

默认服务：`https://ai.weiloo.com/v1`

默认模型：`gpt-image-2.5`

## 使用

### 生成图片

在 Codex 中输入：

```text
$weiloo-image 一只宇航员猫，漂浮在月球表面，写实摄影风格
```

在 WorkBuddy 中输入：

```text
/weiloo-image 一只宇航员猫，漂浮在月球表面，写实摄影风格
```

图片会保存到当前任务的 `outputs/` 文件夹。

### 根据附件编辑图片

先在对话中上传一到四张图片，再说明要怎样修改。第一张图片默认是主体，后面的图片默认只作参考。

Codex 示例：

```text
$weiloo-image 保留第一张图的主体；第二张图只参考夜景背景；第三张图只参考金属材质。不要添加文字。
```

WorkBuddy 示例：

```text
/weiloo-image 保留第一张图的主体；第二张图只参考夜景背景；第三张图只参考金属材质。不要添加文字。
```

如果当前对话没有提供可读的本地附件路径，Skill 会如实说明不能以原图编辑，不会生成一张相似的新图并说它是编辑结果。生成结果由服务端返回，尺寸可能与请求尺寸不同。

## 图片失败

生成或编辑失败后会留下本地诊断报告。可输入：

```text
Codex：$weiloo-image 为什么生成失败
WorkBuddy：/weiloo-image 为什么生成失败
```

诊断只检查本地配置和模型接口，不会重新生成或重新编辑图片。
