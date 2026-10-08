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
- 输入组合选择。适配层只列出同时包含 `before.tif`、`after.tif`、`mismatch.tif` 的 fixture 目录。
- 阶段进度：排队、启动、发现工具、模型规划、成功链（含当前工具和序号）、拒绝链、整理证据、完成。
- Qwen 实际使用的模型与降级信息，以及模型规划出的工具顺序和中文说明。
- 每个工具步骤可以展开查看输入、输出摘要、execution ID、trace ID 和 manifest 内容。
- 输入尺寸或空间范围不一致时的 `shape_mismatch` 拒绝链。
- 运行历史。已完成的运行来自 runner 写入的摘要文件，可重新打开；当前进程内失败的任务也会列出原因。

当前输入仍是小型合成工程 GeoTIFF。页面显示的水体标签和面积统计不能解释为真实洪水因果或卫星观测性能。

## 适配层接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/water-demo/health` | 服务状态和当前是否有任务在运行 |
| GET | `/api/water-demo/fixtures` | 可选输入组合 |
| POST | `/api/water-demo/run` | 提交任务，body 为 `{prompt, fixture_id, messages}`，返回 `202` 和 `job_id`；已有任务运行时返回 `409` |
| GET | `/api/water-demo/jobs/{job_id}` | 任务阶段、当前工具和完成后的运行摘要 |
| GET | `/api/water-demo/history?limit=N` | 运行历史 |
| GET | `/api/water-demo/runs/{run_id}` | 单次运行摘要 |
| GET | `/api/water-demo/runs/{run_id}/evidence` | 每个步骤的输入、输出、manifest 内容和 execution history 匹配情况 |

同一时间只运行一个任务。任务状态保存在适配层进程内存里，重启后只保留已写入磁盘的运行摘要。阶段进度由 runner 在阶段和工具边界写入状态文件、页面每 0.7 秒轮询得到，不是 Terrabox 的流式执行反馈；合成 fixture 上整条链只需几秒，页面通常只能看到其中部分工具。

页面请求统一经过 `apiFetch`，因为 Terraflow 的全局 fetch 拦截器会写入 `config.headers`，不带 init 参数的 `fetch(url)` 会在拦截器里报错。

## 与命令行演示的关系

网页适配层通过子进程传入 `--prompt`、`--fixtures-dir` 和 `--progress-file`，复用 `scripts/terraflow_water_demo.py`，因此 SSH 命令行和网页入口使用相同的工具发现、Qwen 规划、profile/grid 门控以及 manifest/trace/provenance 记录。不传 `--progress-file` 时，命令行行为与之前相同。
