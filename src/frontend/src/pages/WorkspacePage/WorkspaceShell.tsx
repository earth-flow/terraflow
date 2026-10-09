import { NavLink, Outlet, useNavigate } from "react-router-dom";
import TerraflowLogo from "@/assets/TerraflowLogo.svg?react";
import { useLogout } from "@/controllers/API/queries/auth";
import "./workspace.css";

const NAV = [
  {
    to: "/workspace",
    label: "对话",
    end: false,
    match: (path: string) =>
      path === "/workspace" || path.startsWith("/workspace/c/"),
  },
  { to: "/workspace/data", label: "数据" },
  { to: "/workspace/scenarios", label: "场景" },
  { to: "/workspace/flows", label: "Flow" },
  { to: "/workspace/tools", label: "工具" },
  { to: "/workspace/runs", label: "运行记录" },
];

export default function WorkspaceShell() {
  const navigate = useNavigate();
  const { mutate: logout } = useLogout();
  const path = window.location.pathname;
  return (
    <div className="ws-app flex h-screen w-full flex-col bg-background">
      {/* On narrow screens the navigation wraps onto its own row. */}
      <header className="ws-topbar flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2 md:h-12 md:flex-nowrap md:py-0">
        <button
          type="button"
          className="flex items-center gap-2 pr-3"
          onClick={() => navigate("/workspace")}
        >
          <TerraflowLogo className="h-5 w-5" />
          <span className="text-sm font-semibold">Agent Workspace</span>
        </button>
        <nav
          aria-label="工作区导航"
          className="order-last flex w-full min-w-0 gap-1 overflow-x-auto md:order-none md:w-auto md:flex-1"
        >
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to !== "/workspace"}
              className={({ isActive }) =>
                "shrink-0 rounded-lg px-3 py-1.5 text-sm " +
                ((item.match ? item.match(path) : isActive)
                  ? "ws-nav-item-active"
                  : "ws-nav-item")
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <a
          href="/flows"
          style={{ marginLeft: "auto" }}
          className="shrink-0 rounded-lg px-3 py-1.5 text-sm ws-nav-item"
          title="Terraflow 原有的 flow 列表与 Canvas 编辑器"
        >
          Canvas 编辑器
        </a>
        <button
          type="button"
          className="shrink-0 rounded-lg px-3 py-1.5 text-sm text-muted-foreground ws-nav-item"
          onClick={() => logout()}
        >
          退出登录
        </button>
      </header>
      <main className="min-h-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
