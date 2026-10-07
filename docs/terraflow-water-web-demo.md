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

- 自然语言任务输入和运行状态。
- Qwen 实际使用的模型与降级信息。
- 模型规划出的工具顺序。
- 成功链中每个工具的 execution ID、trace ID、manifest 路径。
- 输入尺寸或空间范围不一致时的 `shape_mismatch` 拒绝链。
- Terrabox execution history 的匹配情况。

当前输入仍是小型合成工程 GeoTIFF。页面显示的水体标签和面积统计不能解释为真实洪水因果或卫星观测性能。

## 与命令行演示的关系

网页适配层通过子进程传入 `--prompt`，复用 `scripts/terraflow_water_demo.py`，因此 SSH 命令行和网页入口使用相同的工具发现、Qwen 规划、profile/grid 门控以及 manifest/trace/provenance 记录。后续可以在该页面上增加文件选择、任务历史、证据面板和 Canvas 节点视图。
