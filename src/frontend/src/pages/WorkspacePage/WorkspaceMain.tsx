import { useEffect } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import ChatPage from "./ChatPage";
import DataStage from "./DataView";
import FlowView from "./FlowView";
import ToolsView from "./ToolsView";
import { useWorkspace, type View } from "./workspaceStore";

/** The workspace: the assistant panel on the left, one of three views beside it. */
export default function WorkspaceMain() {
  const view = useWorkspace((s) => s.view);
  const setView = useWorkspace((s) => s.setView);
  const [params] = useSearchParams();
  useEffect(() => {
    const requested = params.get("view");
    if (requested === "data" || requested === "tools" || requested === "flow")
      setView(requested);
  }, [params, setView]);
  return (
    <div className="ws-main">
      <div className="ws-glass ws-left">
        <ChatPage />
      </div>
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
