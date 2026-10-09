# Terraflow Agent Workspace

该演示在 Terraflow 官方 React/TypeScript/Vite 前端中提供一个自定义对话入口。页面提交的自然语言任务会进入同一条水体 runner，后端适配层再调用 Terrabox 和 llama.cpp，并返回工具规划、执行记录、拒绝原因和追踪索引。

## 启动

在 L20 上执行：

```bash
cd /data2/liyezhuoxin/terraflow-water-demo

# 适配层只监听本机，浏览器不直接访问 Terrabox、llama.cpp 或栅格文件
nohup python3 scripts/terraflow_water_web_api.py \
  > /tmp/terraflow-water-web.log 2>&1 </dev/null &

cd src/frontend
VITE_PROXY_TARGET=http://127.0.0.1:7861 \
  nohup npm run start -- --host 0.0.0.0 --port 4173 \
  > /tmp/terraflow-frontend.log 2>&1 </dev/null &
```

如果服务已经在运行，先查看端口：

```bash
ss -ltn | grep -E ':(7861|7862|4173) '
curl http://127.0.0.1:7862/api/water-demo/health
```

本机做 SSH 转发：

```bash
ssh -N -L 14173:127.0.0.1:4173 L20X4-bhc
```

然后访问 [http://127.0.0.1:14173/workspace](http://127.0.0.1:14173/workspace)。

## 工作区结构（2026-10-09 起）

`/` 默认进入 Agent Workspace（`/workspace`）。它有自己的顶栏和导航，不再嵌在 Langflow 的页面框架中：

| 页面 | 路径 | 内容 |
| --- | --- | --- |
| 对话 | `/workspace`、`/workspace/c/:id` | 左侧会话列表；中间消息流；底部输入框，可用“＋ 添加数据”附加数据集 |
| 数据 | `/workspace/data` | 数据集卡片、来源、上传；“用这组数据对话”会带着该数据打开对话 |
| 场景 | `/workspace/scenarios` | 已注册场景的输入、输出、工具和限制 |
| Flow | `/workspace/flows` | 带 `agent-workspace` 标签的 flow（旧的按名称前缀兼容） |
| 工具 | `/workspace/tools` | Terrabox 工具目录、可用状态、被哪些场景使用、按输入类型的参数预设 |
| 运行记录 | `/workspace/runs`、`/workspace/runs/:id` | 历史运行与通用结果详情 |

顶栏的“Canvas 编辑器”进入 Terraflow 原有的 flow 列表（`/flows`）；Langflow 顶栏新增“← 工作区”，左上角图标也回到工作区。

### 一轮对话

1. 用户消息先由规划模型判断：直接回答（基于对话、数据目录、场景说明和上一次结果），还是提出运行。
2. 提出运行时，先确定数据：附加的数据集、消息中原文提到且与数据描述一致的数据集、或上一次运行用过的数据集；都没有时请用户指定。然后生成计划卡片（场景、数据及其来源、工具顺序、说明），等待用户点“执行”。
3. 执行时 runner 读取确认过的计划（`--plan-file`，重新校验顺序规则但不重新规划），逐步写事件（`--events-file`）；适配层把阶段和每个工具的开始/完成推送给页面。完成后按场景生成统一结果（标题句、数值、图层与图例、网格检查、门控自检、适用边界），再流式输出一段基于结果的解读。
4. 回答和解读中的数字会与给定事实核对（允许四舍五入以及比例、百分比和差值），不在其中的会提示“以结果卡片中的数值为准”。

### 场景注册

`scripts/workspace_scenarios.py` 的 `TASK_SCENARIOS` 声明每个场景的输入、要求、输出、工具、限制、示例和结果构建函数。页面只渲染统一结果，新增场景不需要新的前端组件。目前注册了“水体变化”。

### 流式接口

前缀 `/api/workspace`（`/api/water-demo` 保留为别名）。流式接口返回 `text/event-stream`，事件为 `status`、`token`、`step`、`result`、`message`、`error`、`done`，并带 `Cache-Control: no-transform`，以免预览服务器的压缩缓冲。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/scenarios` | 场景目录 |
| GET/POST | `/conversations` | 会话列表（不含空会话）/ 新建 |
| GET/DELETE | `/conversations/{id}` | 读取 / 删除会话（不删除运行结果和 flow） |
| POST | `/conversations/{id}/messages` | 发送消息，流式返回回答或计划 |
| POST | `/conversations/{id}/plans/{message_id}/execute` | 执行计划（body `{self_check}`），流式返回步骤、结果和解读 |
| POST | `/conversations/{id}/plans/{message_id}/cancel` | 取消计划 |
| GET | `/runs/{run_id}/result` | 通用结果 |

## 页面能展示什么

- 会话消息列表。每次提交时，最近 6 条消息会作为上下文传给 Qwen 规划；超出长度时先丢弃最早的消息，当前任务不会被截断。
- 分析数据以卡片显示前期、后期影像缩略图、标题、地点、时间、传感器和范围；默认只列卫星观测和用户上传的数据，合成测试栅格和测试上传归为“开发者数据”，勾选后才显示。
- 运行前核对数据：页面把任务描述交给本机 Qwen 识别其中提到的地点或事件（见下文），与当前选择不一致时提示改用对应数据；提到的地点不在现有数据中时提示上传。
- 阶段进度：排队、启动、发现工具、模型规划、执行分析（含当前工具和序号）、门控自检（仅勾选时）、整理证据、完成。
- 影像与水体：前期、后期影像和由阈值得到的两期水体（SAR 输入），或两期水体标签（标签输入）。
- Qwen 实际使用的模型与降级信息，以及模型规划出的工具顺序和中文说明。
- 每个工具步骤可以展开查看输入、输出摘要、execution ID、trace ID 和 manifest 内容。
- 门控自检（可选）：勾选“同时运行门控自检”后，用数据附带的、故意做成尺寸不一致的 `mismatch.tif` 再做一次网格检查，预期被 `shape_mismatch` 拦截。默认不运行；成功链中的门控始终生效。
- 运行历史。已完成的运行来自 runner 写入的摘要文件，可重新打开；当前进程内失败的任务也会列出原因。
- 水体统计卡片：有效面积，前期、后期、新增、消退水体面积；SAR 输入还会显示所用的 dB 阈值。

## 输入组合

输入组合来自两个目录，每个目录必须包含 `before.tif` 和 `after.tif`，`mismatch.tif` 可选（只用于门控自检）。目录中的 `fixture.json` 说明输入类型和来源；没有这个文件的 `demo-fixtures/` 根目录按合成 0/1 水体标签处理。

| 来源 | 位置 | 说明 |
| --- | --- | --- |
| 合成 | `runtime/demo-fixtures/` | 2×2 工程栅格，只用于检查工具链 |
| 真实观测 | `runtime/demo-fixtures/<name>/` | 由 `scripts/fetch_sentinel1_rtc_pair.py` 生成，带 STAC item、时相、轨道和网格记录 |
| 上传 | `runtime/web-uploads/upl_*/` | 页面上传，`fixture.json` 记录原文件名、大小、sha256 和栅格检查结果 |

`input_kind` 为 `water_labels` 时，两期栅格直接作为 0/1 标签比较；为 `sar_backscatter` 时，需要先由 Terrabox 的通用算子 `geo_raster.threshold_segmentation` 生成水体标签。

规划器只决定是否执行这一步；怎么执行由 runner 中 `SCENARIOS` 的场景预设固定，不交给模型选择。SAR 的预设是 `sar_low_backscatter_water`：线性值先转成 dB，**低于**阈值的像素记为水体；后期影像用 Otsu（−35～5 dB 直方图）求阈值，前期影像沿用同一阈值；两期都输出 0=非水体、1=水体、255=nodata，再交给 `geo_water.water_evidence`。运行摘要的 `success_chain.derived_labels` 记录所用预设、阈值和单位。

新的输入类型（例如光学 NDWI 水体：高于 0 的像素记为水体）只需在 `SCENARIOS` 增加一条预设并准备测试数据，不需要新写 Terrabox 工具。只有运算本身是新的（配准、阴影掩膜、HAND、永久水体剔除等）才需要新工具。

科学门控拦截（例如上传的两景尺寸不同）是正常结果：运行状态为完成，成功链状态为 `blocked_by_gate`，页面显示被拦截的工具和原因。

模型规划中重复出现的工具只执行一次，去掉的重复项记录在运行摘要的 `plan.removed_duplicates`。

## 真实 Sentinel-1 样例

当前样例是 2023 年 9 月风暴 Daniel 期间希腊色萨利平原（Palamas 以北，约 22.005–22.125°E、39.495–39.585°N），Sentinel-1A RTC VV，同为降轨相对轨道 7：

- 前期 `S1A_IW_GRDH_1SDV_20230901T043157_20230901T043222_050129_060869_rtc`
- 后期 `S1A_IW_GRDH_1SDV_20230913T043157_20230913T043222_050304_060E62_rtc`

生成命令（需要带 rasterio 的环境，只按窗口读取所需范围）：

```bash
/data2/liyezhuoxin/envs/terrabox-water-demo/bin/python scripts/fetch_sentinel1_rtc_pair.py \
  --before-item S1A_IW_GRDH_1SDV_20230901T043157_20230901T043222_050129_060869_rtc \
  --after-item S1A_IW_GRDH_1SDV_20230913T043157_20230913T043222_050304_060E62_rtc \
  --bbox 22.005 39.495 22.125 39.585 \
  --out-dir /data2/liyezhuoxin/terrabox-water-demo/runtime/demo-fixtures/thessaly_2023_s1 \
  --label "色萨利 2023 Sentinel-1（真实）" \
  --event "Storm Daniel, Thessaly plain west of Larissa near Palamas, Greece; rainfall 2023-09-04 to 2023-09-07"
```

脚本要求两景同轨道、同升降轨方向、前期早于后期，把两景双线性重采样到前期影像投影坐标系下按 10 m 对齐的网格，并把任一景无效的像素在两景中都设为 nodata。`mismatch.tif` 是从后期影像裁掉 50 列得到的，用来在真实像素上触发尺寸不一致拒绝。数据来自 Microsoft Planetary Computer 的 `sentinel-1-rtc`（集合元数据标注 CC-BY-4.0，含修改过的 Copernicus Sentinel 数据），影像和运行结果都不提交。

水体掩膜是后向散射阈值估计，不是参考标注；平滑地表、阴影等也可能低于阈值。前后差异本身不能证明洪水因果。

## 适配层接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/water-demo/health` | 服务状态和当前是否有任务在运行 |
| GET | `/api/water-demo/fixtures` | 可选输入组合，含来源、输入类型和影像时相 |
| POST | `/api/water-demo/uploads` | 新建上传，body 为 `{label, input_kind, input_scale}` |
| POST | `/api/water-demo/resolve-input` | body `{prompt, current}`；本机 Qwen 识别任务里提到的数据，返回 `input_set`、`mentioned`、`unmatched` |
| GET | `/api/water-demo/input-sets/{id}/preview/{before\|after\|mismatch}` | 输入影像的 PNG 预览 |
| GET | `/api/water-demo/runs/{run_id}/preview/{before\|after\|before_mask\|after_mask}` | 一次运行的影像与水体预览 |
| PUT | `/api/water-demo/uploads/{upload_id}/{before\|after\|mismatch}` | 上传单个 GeoTIFF 原始字节，单个不超过 64 MB，只接受 TIFF 文件头 |
| POST | `/api/water-demo/uploads/{upload_id}/finalize` | 检查坐标参考系和单波段，通过后加入输入组合 |
| POST | `/api/water-demo/run` | 提交任务，body 为 `{prompt, fixture_id, messages}`，返回 `202` 和 `job_id`；已有任务运行时返回 `409` |
| GET | `/api/water-demo/jobs/{job_id}` | 任务阶段、当前工具和完成后的运行摘要 |
| GET | `/api/water-demo/history?limit=N` | 运行历史 |
| GET | `/api/water-demo/tools` | Terrabox 工具目录（缓存 30 秒）、水体链工具和场景预设 |
| GET | `/api/water-demo/canvas-flows` | 运行或模板（`template:<输入组合>`）与 flow 的对应关系 |
| POST | `/api/water-demo/input-sets/{id}/canvas` | 为输入组合生成模板 flow（每个组合一次，`?force=1` 重新生成） |
| DELETE | `/api/water-demo/canvas-flows/{flow_id}` | 删除 flow 后清理对应关系 |
| GET | `/api/water-demo/runs/{run_id}` | 单次运行摘要 |
| GET | `/api/water-demo/runs/{run_id}/evidence` | 每个步骤的输入、输出、manifest 内容和 execution history 匹配情况 |

同一时间只运行一个任务。任务状态保存在适配层进程内存里，重启后只保留已写入磁盘的运行摘要。阶段进度由 runner 在阶段和工具边界写入状态文件、页面每 0.7 秒轮询得到，不是 Terrabox 的流式执行反馈；合成 fixture 上整条链只需几秒，页面通常只能看到其中部分工具。

页面请求统一经过 `apiFetch`，因为 Terraflow 的全局 fetch 拦截器会写入 `config.headers`，不带 init 参数的 `fetch(url)` 会在拦截器里报错。

## 与命令行演示的关系

上传检查使用 `TERRAFLOW_WATER_GEO_PYTHON` 指定的 Python（默认 Terrabox 环境）打开栅格，适配层本身只用标准库。

网页适配层通过子进程传入 `--prompt`、`--fixtures-dir` 和 `--progress-file`，复用 `scripts/terraflow_water_demo.py`，因此 SSH 命令行和网页入口使用相同的工具发现、Qwen 规划、profile/grid 门控以及 manifest/trace/provenance 记录。不传 `--progress-file` 时，命令行行为与之前相同。

## Canvas flow

每次运行的计划都可以生成一个 Terraflow Canvas flow。结果区“运行 ID”下的“在 Canvas 中打开”会调用 `POST /api/water-demo/runs/{run_id}/canvas`：同一次运行只生成一次，映射记录在 `runtime/canvas-flows.json`，加 `?force=1` 可重新生成。

flow 由 `scripts/build_water_flow.py` 生成（需要 Terraflow 虚拟环境里的 lfx）：读取运行摘要中经过 runner 校验的工具顺序，按 runner 的 `SCENARIOS` 预设填参数，用 lfx 自带的 `build_flow_from_spec` 和线上组件目录拼成 flow，再通过 `POST /api/v1/flows/` 保存。也可以不依赖运行，直接为某个输入组合生成模板：

```bash
.venv/bin/python scripts/build_water_flow.py --input-set thessaly_2023_s1           # 模板
.venv/bin/python scripts/build_water_flow.py --run-id 20261008_154450_c502d2       # 来自一次运行
.venv/bin/python scripts/build_water_flow.py --input-set demo-fixtures --dry-run   # 只打印规格
```

flow 里的节点都是真实组件：

| 节点 | 来源 | 作用 |
| --- | --- | --- |
| Water Input Set | `lfx/components/geo_workflow`（Geo Workflow 类别） | 选择 `$TERRAFLOW_WATER_RUNTIME_DIR` 下的输入组合，输出前后影像和拒绝样本路径；没有拒绝样本时停止该分支 |
| Raster Pair Gate | 同上 | 读取 Raster Profile Preflight 的结果；不通过时停止前后路径两个输出，下游成对分析不执行，Decision 输出原因 |
| Tool Result Field | 同上 | 从 Terrabox 工具结果中取出 `outputs.output_path`、`outputs.threshold` 等字段，交给下一个工具 |
| Inspect GeoTIFF、Raster Profile Preflight、Threshold Segmentation、Raster Difference、Water Label Evidence、Pixel → Area | Terrabox 自动生成的组件 | 与 runner 调用的是同一组 Terrabox 工具 |

SAR 输入时，flow 中有两个 Threshold Segmentation：后期影像用 Otsu，求出的阈值经 Tool Result Field 传给前期影像，与 runner 的规则一致。只有做过门控自检的运行（或模板加 `--self-check`）生成的 flow 才带拒绝分支，其中的 Water Label Evidence 在门控拦截后不会执行。

后端需要设置 `TERRAFLOW_WATER_RUNTIME_DIR`（Water Input Set 用它列出输入组合）。新组件已写入 `src/lfx/src/lfx/_assets/component_index.json`，生产模式无需 `LFX_DEV`。

## 影像预览与数据识别

预览由 `scripts/render_raster_preview.py` 在 Terrabox geo 环境中渲染：SAR 转为 dB 后按 −25～0 dB 拉伸为灰度，0/1 标签中水体为蓝色、非水体为灰色，无效像素透明；只渲染 runtime 目录内的文件，按文件路径、修改时间和大小缓存在 `runtime/previews/`。

数据识别调用本机规划模型（默认 Qwen3.8-27B），要求它原文摘出任务中提到的地点、事件或数据类型。适配层只在摘出的文字确实出现在任务中、且与所选数据的标题、地点或事件有重合时才采用；否则视为“没有提到具体数据”，沿用当前选择。任务中出现“合成”或 “synthetic” 时直接对应合成测试栅格。识别结果和用户的选择（改用、沿用、未提及）记录在运行摘要的 `web_request.input_resolution`。

数据卡片的标题、地点、传感器和类别来自 `fixture.json` 的 `title`、`place`、`sensor`、`category`；`category` 为 `synthetic` 或 `test` 的数据归为开发者数据。

## 登录与访问控制

Terraflow 关闭自动登录（`TERRAFLOW_AUTO_LOGIN=false`）时，适配层对除 `/api/water-demo/health` 以外的所有请求校验 Terraflow 会话：从 `Authorization: Bearer` 或 `access_token_lf` cookie 取出令牌，调用 `/api/v1/users/whoami` 核对，结果缓存 60 秒，未登录返回 401（影像预览同样受保护）。可用 `TERRAFLOW_WATER_REQUIRE_LOGIN=0` 关闭，仅限只监听本机的开发环境。

生成 Canvas flow 时，适配层把发起请求的用户令牌通过 `TERRAFLOW_ACCESS_TOKEN` 传给 `scripts/build_water_flow.py`，flow 建在该用户名下；脚本单独运行时依次尝试自动登录和 `~/.config/earthmind/terraflow-login.json`（或 `TERRAFLOW_LOGIN_FILE`）中的账号。

通过普通 http（非 localhost）访问时，需设置 `TERRAFLOW_REFRESH_SECURE=false`、`TERRAFLOW_REFRESH_SAME_SITE=lax`，否则浏览器会丢弃刷新令牌 cookie，登录一小时后失效。
