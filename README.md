# Weiloo Image Skill

## Codex 安装

在 Codex 中复制并发送：

```text
安装这个 Skill：
https://github.com/weilooAi/weiloo-image-skill
```

## WorkBuddy 安装

在 WorkBuddy、workbuddy、workBuddy 或 WORKBUDDY 中复制并发送：

```text
在 WorkBuddy 安装这个 Skill：
https://github.com/weilooAi/weiloo-image-skill
```

## 配置

首次使用会自动提示输入 API Key，不需要修改配置文件或设置环境变量。

为避免密钥出现在聊天记录、命令历史或 Git 中，不要把真实 API Key 直接附在安装消息里。

## 使用

在 Codex 中输入：

```text
$weiloo-image 一只weiloo在跳舞
```

在 WorkBuddy 中直接输入：

```text
/weiloo-image 一只宇航员猫
```

图片会保存到当前任务的 `outputs` 文件夹中。

## 图片失败

生成失败后会给出本地诊断报告。可输入：

```text
Codex：$weiloo-image 为什么生成失败
WorkBuddy：/weiloo-image 为什么生成失败
```

诊断只检查本地配置和模型接口，不会重新生成图片。
