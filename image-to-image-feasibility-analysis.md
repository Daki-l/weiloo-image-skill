# Weiloo Image Skill 图生图与多图编辑可行性分析

**分析日期：** 2026-09-23
**分析范围：** `E:\program\ai\syber-gpt-image-2` 与当前 `weiloo-image-skill` 的静态源码对比，以及一次经用户授权的 Weiloo 多图编辑实机验证。
**实机验证输入：** 两张程序生成的无敏感 1024×1024 PNG；未使用、输出或记录任何 API Key。

## 结论

可以实现，而且不需要把当前纯 Skill 改造成 Web 服务、Plugin、MCP 或 Node 项目。

参考项目能够图生图的根本原因，是它将源图作为二进制文件，以
`multipart/form-data` 发送到 OpenAI 兼容的 `POST /images/edits`；当前 Skill 则始终以
JSON 调用 `POST /images/generations`，没有接收源图路径，也没有构造 multipart 请求。

因此，当前 WorkBuddy 的拒绝是代码行为的直接结果，而不是“Skill 天生不能图生图”。截图中
“当前图片编辑接口没有提供以原图为输入的参数”的说明，与当前实现完全一致。

| 判断项 | 结论 | 依据 / 前提 |
| --- | --- | --- |
| Python Skill 增加多图编辑命令的可行性 | 高 | 现有脚本已经负责配置、HTTP、响应解码、下载和原子写入；只需增加本地图片读取和 multipart 请求。 |
| 参考项目的图生图机制 | 已证实 | 它的 provider 调用 `/images/edits`，并以 `files` 上传一个或多个 `image` 字段。 |
| `https://ai.weiloo.com/v1` + `gpt-image-2.5` 是否支持双图编辑 | 已证实 | 2026-09-23 以当前本地 Key 发起了一次双 `image` multipart 请求并获得有效 PNG 结果。 |
| Codex / WorkBuddy 附件是否能交给本地脚本 | 当前目标环境已证实 | Codex 当前任务中的附件路径可读；WorkBuddy 已返回并验证了一条可读的本地绝对路径。 |

核心 API 验证已经完成。建议直接实现“**非电商、有序多图编辑**”的 Skill：支持两到四张本地
参考图，第一张默认是主体图，其余为参考图；不实现商品分析、详情页规划、批量系列图或 Web 功能。

## 实机验证结果（已完成）

本次验证使用当前本机配置中的 `https://ai.weiloo.com/v1` 与 `gpt-image-2.5`，发起了**一笔**可能
计费的请求：

```text
POST /v1/images/edits
Content-Type: multipart/form-data

普通字段：model、prompt、size=1024x1024
文件字段：image=reference-blue.png、image=reference-gold.png
```

两张输入图都是程序内存中生成的纯色 1024×1024 PNG，不包含用户上传内容。请求成功后输出了一张
有效 PNG：

| 项目 | 实测结果 |
| --- | --- |
| 重复 `image` 字段数 | 2 |
| 编辑端点 | `/images/edits` |
| 模型 | `gpt-image-2.5` |
| 返回文件 | `outputs/weiloo-multi-image-edit-validation-20260923-171932-8bf15f.png` |
| PNG 签名 / IHDR | 有效 PNG / 有效 IHDR |
| 返回尺寸 | 1254×1254 |
| 返回文件大小 | 1,881,474 bytes |
| 画面检查 | 可见蓝色与金色的抽象组合，无可读文字或水印。 |

这确认了当前 Key 在测试时拥有双图编辑权限，且服务端接受重复的 `image` multipart 文件字段。
但也发现服务端没有严格按请求的 `1024x1024` 返回结果，而是返回 `1254×1254`。后续 Skill 应保存
实际返回文件，不应承诺结果尺寸必然等于请求尺寸。

本次验证没有证明 `mask`、三张以上图片、远程 URL、任意文件格式或每一种可选请求字段都受支持。

## 参考项目如何做到图生图

参考项目是 React + FastAPI 的完整网站，但图生图的核心链路很小：

```text
浏览器选择源图和编辑提示词
        |
        v
前端 FormData：重复 image 文件字段 + prompt 等普通字段
        |
        v
FastAPI /api/images/edit：保存上传图并创建任务
        |
        v
后台任务读取源图字节
        |
        v
OpenAICompatibleImageClient：POST <base_url>/images/edits
multipart/form-data：image 文件 + model/prompt/size/... 字段
        |
        v
读取 b64_json 或 url 响应，保存结果图
```

### 1. 前端用 FormData 上传实际文件

`src/api.ts:712-737` 的 `appendReferenceInputs()` 对每张参考图执行：

```ts
form.append('image', reference.file)
```

浏览器因此把原始图片字节传给后端，而不是只把文件名或图片描述传过去。该项目还会把每张图的
用途写入 `reference_notes`，帮助模型区分主图、角度图和风格参考图；这属于多图体验增强，不是
单图编辑的必要条件。

### 2. 后端接收文件，建立编辑任务

`backend/app/main.py:1312-1365` 的 `/api/images/edit` 接口要求：

- `prompt`：普通表单字段；
- `image`：`list[UploadFile]`，可以重复上传多张；
- `mask`：可选文件；
- `model`、`size`、`quality`、`n`：普通表单字段。

它把上传文件保存到 `backend/storage/uploads`，建立异步任务。任务执行时，
`backend/app/main.py:3786-3815` 将保存文件重新读成
`(filename, bytes, content_type)` 元组。

### 3. 真正的上游编辑请求是 `/images/edits`

决定性代码在 `backend/app/provider.py:40-52`：

```python
files = [
    ("image", (filename, content, content_type))
    for filename, content, content_type in images
]
response = await self._request(
    config,
    "POST",
    "/images/edits",
    data=fields,
    files=files,
)
```

`httpx` 会据此生成 `multipart/form-data` 请求及 boundary。业务字段包含
`model`、`prompt`、`size`、`quality`、`n` 和 `response_format=b64_json`
（`backend/app/main.py:1334-1344`）。

这与文生图调用 `/images/generations` 是两条不同的 API 路径。参考项目 README 也明确区分：

- 文生图：`/v1/images/generations`；
- 改图：`/v1/images/edits`；
- 改图上传：`multipart` 的一张或多张 `image`。

对应说明位于 `E:\program\ai\syber-gpt-image-2\README.md:18-21` 与 `:125-131`。

### 4. 响应处理已可复用

参考项目会接受 `b64_json` 或 `url`，保存结果图（`backend/app/storage.py:45-65`）。
当前 Skill 已有同样的结果兼容能力：`scripts/imagegen.py:317` 的
`image_bytes_from_response()` 同时处理 `b64_json` 和 `url`，并对下载体积设有上限、以原子方式写入
`outputs/`。这一部分无需为图生图重新设计。

### 5. 不应复制的部分

以下属于网站产品能力，不是让图生图成立的必要条件：

- React 页面、文件上传组件和电商详情页界面；
- FastAPI、SQLite、用户系统、账单和历史记录；
- 异步任务中心、队列、自动重试和批量系列图规划；
- 多参考图角色说明、视觉分析和提示词规划；
- Docker、Nginx 与 Android 工程。

当前项目定位是本地 Codex / WorkBuddy Skill。搬运这些组件会破坏其“简单、稳定、无需部署”的目标，
且无法解决上游 API 是否支持编辑的问题。

## 实施前：Skill 为什么不能图生图

V1 实施前的限制可直接定位：

| 实施前位置 | 原始情况 | 图生图影响 |
| --- | --- | --- |
| `scripts/imagegen.py:185-202` | `build_generation_request()` 固定向 `/images/generations` 发送 JSON。 | 不会调用编辑端点。 |
| `scripts/imagegen.py:197` | 请求头固定为 `Content-Type: application/json`。 | 不能携带文件 multipart。 |
| `scripts/imagegen.py:582-601` | CLI 只有 prompt、size、output、configure、diagnose。 | 没有 `--image` 或 `--mask` 源图输入。 |
| `SKILL.md:26-30` | Skill 固定运行 `imagegen.py --prompt "<用户图片描述>"`。 | 聊天附件从未被传给脚本。 |
| `scripts/imagegen.py:317` | 已支持 `b64_json` 与 URL 结果。 | 结果保存逻辑可复用。 |

实施前的 `SKILL.md` 虽然将“图片编辑”列为触发场景，但实际命令仍是文生图命令。这是提示词声明与
脚本能力不一致，不是 WorkBuddy 的 `/` 与 Codex 的 `$` 调用符号造成的问题。

## 实施前的推荐方案

### V1：有序多图编辑，不包含电商模式

V1 的目标是让用户直接用多张本地图片编辑一张结果图，例如“第一张是商品主体，第二张是背景风格，
第三张是材质参考”。它不分析商品、不生成详情页方案、不拆分系列图。实施前建议使用可重复的 `--image`：

```text
python "<Skill 目录>/scripts/imagegen.py" \
  --prompt "第一张图是主体商品，保留其轮廓和纹理；第二张图只参考温馨客厅背景；第三张图只参考木质材质。不要添加文字。" \
  --image "<主体图绝对路径>" \
  --image "<背景参考图绝对路径>" \
  --image "<材质参考图绝对路径>"
```

脚本行为：

1. 不带 `--image` 时，保留当前 `/images/generations` 文生图逻辑。
2. 带一个或多个 `--image` 时，按命令出现顺序读取文件，并构造 `POST {base_url}/images/edits` 的 multipart 请求。
3. 每个 `--image` 产生一个同名 `image` 文件 part，顺序不变；第一张默认为主体图，后续图片为参考图。
4. V1 限制为最多 4 张图；第一版已确认 2 张图兼容，3 至 4 张图需要在代码实现后的受控验收中确认。
5. multipart 至少传递 `image`、`model`、`prompt`、`size`；已验证的核心字段保持最少，不预先添加 `quality`、`n`、`response_format` 或 `mask`。
6. 复用当前 URL / `b64_json` 解析、50 MiB 输出上限、原子写入和 `IMAGE_PATH=` 输出；以实际返回尺寸为准。
7. 一次用户请求只提交一次编辑调用；失败后写诊断报告，不自动重试，避免重复计费。

Python 标准库足以构造 multipart body，因此 V1 不需要 `requests`、`httpx`、Pillow 或新依赖。
未来如需裁剪、透明 mask 预处理或格式转换，再评估 Pillow；不要因为上传文件而引入它。

### Skill 指令的最小补充

仅在用户明确要求编辑、换背景、保留主体、根据附件修改图片或组合多张参考图等情形，且宿主已提供
可访问的本地附件路径时，才调用重复的 `--image`：

```text
Codex：$weiloo-image 把这张图背景换成温馨客厅
WorkBuddy：/weiloo-image 把这张图背景换成温馨客厅
```

对助手应增加以下约束：

- 使用宿主明确提供的附件本地路径；不要扫描工作目录猜测文件。
- 多图默认按附件或命令中出现的顺序处理：第一张是主体，后续图片是参考；用户有其他角色要求时，把角色写入 prompt。
- 没有路径时，明确说明当前任务没有提供可读路径；不要重新生成相似图片并声称完成编辑。
- 不要把图片内容、完整本地路径、API Key 或上游完整错误体写入诊断报告。
- 第一版不接收远程 URL 作为 `--image`，避免新增下载、重定向和 SSRF 风险。

### 输入安全与稳定性

参考项目的 `save_upload()` 直接读取并写入上传内容（`backend/app/storage.py:20-31`），不应原样复制到
本地 Skill。V1 在发送前应检查：

- 文件存在、是常规文件且可读；
- 最多 4 张输入图；单个输入文件大小上限例如 20 MiB，所有输入图总和上限例如 40 MiB；
- 仅接受已确认由上游支持的 PNG、JPEG、WebP，并检查文件签名，不只信任扩展名；
- 使用安全 multipart 文件名，不把用户本地路径放进 HTTP header；
- 区分“源图读取失败”“输入格式不支持”“编辑接口拒绝”和“输出写入失败”；
- 继续隐藏 Python traceback、API Key 和原始服务响应。

`mask`、五张以上图片、批量 `n` 和电商规划可列为后续阶段，不应阻塞多图编辑 V1。

## 已验证与仍需验证的前提

### 已验证 A：Weiloo 双图编辑兼容性

实机验证已确认 `gpt-image-2.5` 能够以当前本地凭据处理 `POST /v1/images/edits`，并接受两个重复
`image` 文件字段。参考项目的核心设计可直接借鉴为 Skill 的 multipart 编码方式，但不需要其 Web 架构。

仍未验证的 API 边界如下：

- 三到四张图片是否与双图行为一致；
- JPEG、WebP、透明 PNG 和 `mask` 的接受范围；
- 可选的 `quality`、`n`、`response_format` 字段；
- 服务端可能规范化请求尺寸的具体规则。

这些都不是实现两图到四图 Skill 的阻塞项，但实现后应各做一次受控验收，不应凭推测承诺。

### 已验证 B：Codex 与 WorkBuddy 的附件本地路径

Skill 脚本只能上传文件字节，不能从聊天中的“图片名称”恢复用户原图。因此附件路径必须由宿主实际
提供，且不能通过扫描目录猜测。

当前目标环境已有两项实测证据：

| 宿主 | 实测结果 | 结论 |
| --- | --- | --- |
| Codex Desktop | 本次任务中的三张附件均以本地绝对路径进入上下文，并已通过 `Test-Path` 存在性检查。 | 当前 Codex 任务可将附件交给本地脚本。 |
| WorkBuddy | 用户按无 API、无上传的只读诊断提示执行；返回结果确认附件有真实本地绝对路径、`存在=true`、`可读=true`、大小为 94,459 bytes。 | 当前 WorkBuddy 任务可将附件交给本地脚本。 |

这意味着当前 Codex 和 WorkBuddy 环境均满足多图编辑的文件交接前提。实现时仍需遵守：只使用宿主
明确提供的路径，不把路径写进诊断报告，不扫描下载目录、临时目录或工作目录寻找同名文件。

## 建议的测试范围

实现前后应保持所有自动化测试不调用真实 API：

| 测试 | 断言 |
| --- | --- |
| 多图 multipart 请求单测 | URL 为 `/images/edits`；`Content-Type` 含 boundary；每张源图以一个顺序正确的 `image` part 出现；API Key 不出现在输出。 |
| 多图编辑响应单测 | 模拟 `b64_json` 与 URL 两种响应都能写入 `outputs/`，不假定返回尺寸与请求尺寸相同。 |
| 输入校验单测 | 不存在文件、目录、超限文件、伪造扩展名和不支持格式都给出普通中文错误，不出现 traceback。 |
| 回归测试 | 不带 `--image` 时仍请求 `/images/generations`，既有文生图不变。 |
| 图像顺序单测 | 三张输入图按 `--image` 的顺序写入 multipart；第一张主体、后续参考的提示词规则不被颠倒。 |
| 不重试测试 | 编辑失败只发出一次请求，并产出 `DIAGNOSTIC_REPORT=`。 |
| 实机兼容验收 | 双图已完成；代码实现后仅在授权下补测三图和四图，确认上限。 |

## 实施前的最终建议

**实施前建议实现非电商的多图编辑。** 当时 Codex 和 WorkBuddy 的附件路径交接已经在目标环境中得到验证。

实现本身是小范围且符合当前项目定位的：在 `scripts/imagegen.py` 增加可重复 `--image` 分支、输入
安全校验、multipart 编码和测试，并在 `SKILL.md` 说明附件路径和顺序规则。预计不需要新增服务、界面、
用户配置、环境变量或依赖。

发布条件应满足：

1. 自动化测试覆盖两图、多图顺序、错误处理和文生图回归；
2. 在用户授权下补测三图和四图，确认 V1 的输入上限；
3. `SKILL.md` 明确要求只使用宿主提供的附件路径，并为路径缺失提供诚实的降级提示。

即使在已验证的宿主中，Skill 也应在当前任务没有提供明确路径时诚实地说明不能把用户原图作为
编辑输入。这样既避免“看起来像换背景、实际是重新生成”的误导，也避免在未经同意时消耗用户额度。

## 实施状态（2026-09-23）

上述 V1 已在当前 Skill 中实现，未引入 Web 服务、Plugin、MCP、Node 依赖或电商模式：

- `scripts/imagegen.py` 现在接受可重复的 `--image <本地绝对路径>`。无 `--image` 时仍调用
  `/images/generations`；有一到四张图片时调用 `/images/edits`，以顺序不变的重复 `image`
  multipart 文件字段上传。
- 输入只接受可读的普通 PNG、JPEG、WebP 文件，并通过二进制签名校验；目录、符号链接和其他非常规
  文件会被拒绝。单图上限为 20 MiB，总上限为 40 MiB。multipart 使用 `image-1.png` 这类安全文件名，
  不会发送本地完整路径。
- 编辑结果复用现有的 `b64_json`、URL 下载和原子写入逻辑。服务端实际返回尺寸会被直接保留，
  不会假定它等于请求尺寸。
- 编辑失败不会自动重试，会像文生图一样生成脱敏的本地诊断报告；`--diagnose` 与 `--image`
  同时使用会被拒绝，避免诊断模式误发编辑请求。
- 图片生成和编辑请求的本地 HTTP 等待参数为 300 秒。生成或编辑请求收到 HTTP `504` 时，报告会明确
  这是服务端或中间网关返回的状态，不是本地等待参数触发；结果状态会标记为不确定，并提示先在服务后台
  确认，避免立即重试造成重复扣费。

自动化测试使用 mock，不会调用真实 Weiloo API。实现完成后，本地 `unittest` 回归测试共 29 项通过，
覆盖文生图回归、两图和三图 multipart 顺序、PNG/JPEG/WebP 签名、路径不泄露、普通文件限制、输入错误、
URL 与 `b64_json` 响应、失败不重试和诊断模式限制。

仍未对三张或四张输入图发起新的真实 API 请求，以避免在没有额外授权时消耗额度；该限制与前述
实机双图验证结论一致。
