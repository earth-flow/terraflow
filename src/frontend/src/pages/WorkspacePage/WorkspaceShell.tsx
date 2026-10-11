import { Outlet, useLocation, useNavigate } from "react-router-dom";
import TerraflowLogo from "@/assets/TerraflowLogo.svg?react";
import { useLogout } from "@/controllers/API/queries/auth";
import { useWorkspace, type View } from "./workspaceStore";
import "./workspace.css";

const VIEWS: { id: View; label: string; icon: string }[] = [
  {
    id: "data",
    label: "数据",
    icon: "M1 3.5l4-2 4 2 4-2v9l-4 2-4-2-4 2z M5 1.5v9 M9 3.5v9",
  },
  {
    id: "tools",
    label: "工具",
    icon: "M1.5 1.5h4.5v4.5h-4.5z M8 1.5h4.5v4.5h-4.5z M1.5 8h4.5v4.5h-4.5z M8 8h4.5v4.5h-4.5z",
  },
  {
    id: "flow",
    label: "工作流",
    icon: "M3 5a2 2 0 1 0 0 4a2 2 0 1 0 0-4 M11 1a2 2 0 1 0 0 4a2 2 0 1 0 0-4 M11 9a2 2 0 1 0 0 4a2 2 0 1 0 0-4 M5 6.3L9 3.7 M5 7.7L9 10.3",
  },
];

export default function WorkspaceShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const { mutate: logout } = useLogout();
  const view = useWorkspace((s) => s.view);
  const setView = useWorkspace((s) => s.setView);
  const conversationId = useWorkspace((s) => s.conversationId);
  const onMain =
    location.pathname === "/workspace" ||
    location.pathname.startsWith("/workspace/c/");
  function open(next: View) {
    setView(next);
    if (!onMain)
      navigate(
        conversationId ? "/workspace/c/" + conversationId : "/workspace",
      );
  }
  return (
    <div className="ws-app flex h-screen w-full flex-col">
      <header className="ws-topbar ws-glass">
        <button
          type="button"
          className="ws-logo"
          onClick={() => navigate("/workspace")}
        >
          <TerraflowLogo className="h-5 w-5" />
          <span>Agent Workspace</span>
        </button>
        <nav
          aria-label="视图"
          className={"ws-views" + (onMain ? " has-on" : "")}
          style={
            {
              "--n": VIEWS.length,
              "--i": Math.max(
                0,
                VIEWS.findIndex((v) => v.id === view),
              ),
            } as React.CSSProperties
          }
        >
          {VIEWS.map((v) => (
            <button
              type="button"
              key={v.id}
              className={onMain && view === v.id ? "on" : ""}
              onClick={() => open(v.id)}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
                <path
                  d={v.icon}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.3"
                />
              </svg>
              {v.label}
            </button>
          ))}
        </nav>
        <div className="ws-top-r">
          <button type="button" onClick={() => navigate("/workspace/runs")}>
            运行记录
          </button>
          <a href="/flows" title="Terraflow 原有的 flow 列表与画布编辑器">
            画布编辑器
          </a>
          <span className="ws-live">
            <i />
            服务在线
          </span>
          <button type="button" onClick={() => logout()}>
            退出
          </button>
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
