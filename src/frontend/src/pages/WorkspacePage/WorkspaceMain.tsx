import { useEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import ChatPage from "./ChatPage";
import DataStage from "./DataView";
import FlowView from "./FlowView";
import ToolsView from "./ToolsView";
import { useWorkspace, type View } from "./workspaceStore";

const WIDTH_KEY = "workspace.chatWidth";
const MIN_WIDTH = 320;
const MAX_WIDTH = 720;
const DEFAULT_WIDTH = 400;

function savedWidth() {
  try {
    const value = Number(window.localStorage.getItem(WIDTH_KEY));
    return value >= MIN_WIDTH && value <= MAX_WIDTH ? value : DEFAULT_WIDTH;
  } catch {
    return DEFAULT_WIDTH;
  }
}

function saveWidth(value: number) {
  try {
    window.localStorage.setItem(WIDTH_KEY, String(value));
  } catch {
    // the width is just not remembered
  }
}

const clamp = (value: number) =>
  Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, value));

/** The workspace: the assistant panel on the left (drag its edge to resize), one of three views beside it. */
export default function WorkspaceMain() {
  const view = useWorkspace((s) => s.view);
  const setView = useWorkspace((s) => s.setView);
  const [params] = useSearchParams();
  const [width, setWidth] = useState(savedWidth);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    const requested = params.get("view");
    if (requested === "data" || requested === "tools" || requested === "flow")
      setView(requested);
  }, [params, setView]);

  function startResize(event: React.PointerEvent) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    let latest = startWidth;
    setDragging(true);
    const move = (e: PointerEvent) => {
      latest = clamp(startWidth + e.clientX - startX);
      setWidth(latest);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setDragging(false);
      saveWidth(latest);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <div className={"ws-main" + (dragging ? " is-resizing" : "")}>
      <div className="ws-glass ws-left" style={{ width }}>
        <ChatPage />
      </div>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="拖动调整对话栏宽度"
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={MAX_WIDTH}
        aria-valuenow={width}
        tabIndex={0}
        className="ws-resizer"
        title="拖动调整宽度，双击恢复默认"
        onPointerDown={startResize}
        onDoubleClick={() => {
          setWidth(DEFAULT_WIDTH);
          saveWidth(DEFAULT_WIDTH);
        }}
        onKeyDown={(event) => {
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
          const next = clamp(width + (event.key === "ArrowLeft" ? -20 : 20));
          setWidth(next);
          saveWidth(next);
        }}
      />
      {view === "data" ? (
        <DataStage />
      ) : view === "tools" ? (
        <ToolsView />
      ) : (
        <FlowView />
      )}
    </div>
  );
}

/** Old page addresses open the matching view. */
export function ViewRedirect({ view }: { view: View }) {
  return <Navigate replace to={"/workspace?view=" + view} />;
}
