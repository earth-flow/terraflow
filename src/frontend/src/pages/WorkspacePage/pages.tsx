import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { API, apiJson, useScenes } from "./api";
import DataPanel from "./DataPanel";
import FlowsPanel from "./FlowsPanel";
import HistoryPanel from "./HistoryPanel";
import PageFrame from "./PageFrame";
import ToolsPanel from "./ToolsPanel";
import type { HistoryItem } from "./types";

export function DataPage() {
  const { scenes, collections, refresh } = useScenes();
  const navigate = useNavigate();
  return (
    <PageFrame>
      <DataPanel
        scenes={scenes}
        collections={collections}
        onUploaded={refresh}
        onUse={(ids) =>
          navigate("/workspace?attach=" + ids.map(encodeURIComponent).join(","))
        }
      />
    </PageFrame>
  );
}

export function FlowsPage() {
  const { scenes } = useScenes();
  return (
    <PageFrame>
      <FlowsPanel scenes={scenes} />
    </PageFrame>
  );
}

export function ToolsPage() {
  return (
    <PageFrame>
      <ToolsPanel />
    </PageFrame>
  );
}

export function RunsPage() {
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const navigate = useNavigate();
  const refresh = useCallback(async () => {
    const body = await apiJson<{ runs: HistoryItem[] }>(
      API + "/history?limit=100",
    );
    setHistory(body.runs);
  }, []);
  useEffect(() => {
    void refresh().catch(() => undefined);
  }, [refresh]);
  return (
    <PageFrame>
      <HistoryPanel
        history={history}
        onRefresh={() => void refresh()}
        onOpen={(runId) =>
          navigate("/workspace/runs/" + encodeURIComponent(runId))
        }
      />
    </PageFrame>
  );
}
