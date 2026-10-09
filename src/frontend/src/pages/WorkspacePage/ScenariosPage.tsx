import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { API, apiJson, displayTool } from "./api";
import PageFrame from "./PageFrame";
import type { Scenario } from "./types";

export default function ScenariosPage() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const navigate = useNavigate();
  useEffect(() => {
    void apiJson<{ scenarios: Scenario[] }>(API + "/scenarios").then((body) =>
      setScenarios(body.scenarios),
    );
  }, []);
  return (
    <PageFrame>
      <div>
        <h1 className="text-lg font-semibold">场景</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          场景是 Agent
          能完成的一类分析：需要什么输入、产出什么、用哪些工具、有哪些限制。对话中
          Agent 会按任务自动选择场景。
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {scenarios.map((scenario) => (
          <div
            key={scenario.id}
            className="rounded-2xl border border-border bg-card p-5"
          >
            <p className="font-medium">{scenario.title}</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              {scenario.summary}
            </p>
            <dl className="mt-3 space-y-2 text-xs leading-5">
              <div>
                <dt className="font-medium">输入</dt>
                <dd className="text-muted-foreground">
                  {scenario.inputs.map((item) => item.label).join("、")}；
                  {scenario.requirements.join("；")}
                </dd>
              </div>
              <div>
                <dt className="font-medium">输出</dt>
                <dd className="text-muted-foreground">
                  {scenario.outputs.join("；")}
                </dd>
              </div>
              <div>
                <dt className="font-medium">工具</dt>
                <dd className="flex flex-wrap gap-1">
                  {scenario.tools.map((slug) => (
                    <span
                      key={slug}
                      className="rounded bg-muted px-1.5 py-0.5 font-mono"
                    >
                      {displayTool(slug)}
                    </span>
                  ))}
                </dd>
              </div>
              <div>
                <dt className="font-medium">限制</dt>
                <dd className="text-muted-foreground">
                  {scenario.limitations.join("；")}
                </dd>
              </div>
            </dl>
            <button
              type="button"
              className="mt-3 text-xs text-primary underline"
              onClick={() => navigate("/workspace")}
            >
              用这个场景开始对话
            </button>
          </div>
        ))}
      </div>
    </PageFrame>
  );
}
