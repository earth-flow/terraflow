# Terraflow 水体 Agent 网页演示

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

然后访问 [http://127.0.0.1:14173/water-agent](http://127.0.0.1:14173/water-agent)。

## 页面能展示什么

- 会话消息列表。每次提交时，最近 6 条消息会作为上下文传给 Qwen 规划；超出长度时先丢弃最早的消息，当前任务不会被截断。
- 输入组合选择，按合成、真实观测、上传标注来源；真实观测显示两景的时相、极化和轨道。也可以在页面上传自己的前后 GeoTIFF。
- 阶段进度：排队、启动、发现工具、模型规划、成功链（含当前工具和序号）、拒绝链、整理证据、完成。
- Qwen 实际使用的模型与降级信息，以及模型规划出的工具顺序和中文说明。
- 每个工具步骤可以展开查看输入、输出摘要、execution ID、trace ID 和 manifest 内容。
- 输入尺寸或空间范围不一致时的 `shape_mismatch` 拒绝链。
- 运行历史。已完成的运行来自 runner 写入的摘要文件，可重新打开；当前进程内失败的任务也会列出原因。
- 水体统计卡片：有效面积，前期、后期、新增、消退水体面积；SAR 输入还会显示所用的 dB 阈值。

## 输入组合

输入组合来自两个目录，每个目录必须包含 `before.tif` 和 `after.tif`，`mismatch.tif` 可选（没有时不运行拒绝链）。目录中的 `fixture.json` 说明输入类型和来源；没有这个文件的 `demo-fixtures/` 根目录按合成 0/1 水体标签处理。

| 来源 | 位置 | 说明 |
| --- | --- | --- |
| 合成 | `runtime/demo-fixtures/` | 2×2 工程栅格，只用于检查工具链 |
| 真实观测 | `runtime/demo-fixtures/<name>/` | 由 `scripts/fetch_sentinel1_rtc_pair.py` 生成，带 STAC item、时相、轨道和网格记录 |
| 上传 | `runtime/web-uploads/upl_*/` | 页面上传，`fixture.json` 记录原文件名、大小、sha256 和栅格检查结果 |

`input_kind` 为 `water_labels` 时，两期栅格直接作为 0/1 标签比较；为 `sar_backscatter` 时，runner 先调用 Terrabox 的 `geo_water.sar_water_mask`：后期影像用 Otsu 求 dB 阈值，前期影像沿用同一阈值，两期都输出 0=非水体、1=水体、255=nodata 的标签，再交给 `geo_water.water_evidence`。对 SAR 输入，候选工具里不提供 `geo_raster.threshold_segmentation`，因为它把高于阈值的像素当作前景，方向与 SAR 水体相反。

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
| PUT | `/api/water-demo/uploads/{upload_id}/{before\|after\|mismatch}` | 上传单个 GeoTIFF 原始字节，单个不超过 64 MB，只接受 TIFF 文件头 |
| POST | `/api/water-demo/uploads/{upload_id}/finalize` | 检查坐标参考系和单波段，通过后加入输入组合 |
| POST | `/api/water-demo/run` | 提交任务，body 为 `{prompt, fixture_id, messages}`，返回 `202` 和 `job_id`；已有任务运行时返回 `409` |
| GET | `/api/water-demo/jobs/{job_id}` | 任务阶段、当前工具和完成后的运行摘要 |
| GET | `/api/water-demo/history?limit=N` | 运行历史 |
| GET | `/api/water-demo/runs/{run_id}` | 单次运行摘要 |
| GET | `/api/water-demo/runs/{run_id}/evidence` | 每个步骤的输入、输出、manifest 内容和 execution history 匹配情况 |

同一时间只运行一个任务。任务状态保存在适配层进程内存里，重启后只保留已写入磁盘的运行摘要。阶段进度由 runner 在阶段和工具边界写入状态文件、页面每 0.7 秒轮询得到，不是 Terrabox 的流式执行反馈；合成 fixture 上整条链只需几秒，页面通常只能看到其中部分工具。

页面请求统一经过 `apiFetch`，因为 Terraflow 的全局 fetch 拦截器会写入 `config.headers`，不带 init 参数的 `fetch(url)` 会在拦截器里报错。

## 与命令行演示的关系

上传检查使用 `TERRAFLOW_WATER_GEO_PYTHON` 指定的 Python（默认 Terrabox 环境）打开栅格，适配层本身只用标准库。

网页适配层通过子进程传入 `--prompt`、`--fixtures-dir` 和 `--progress-file`，复用 `scripts/terraflow_water_demo.py`，因此 SSH 命令行和网页入口使用相同的工具发现、Qwen 规划、profile/grid 门控以及 manifest/trace/provenance 记录。不传 `--progress-file` 时，命令行行为与之前相同。
