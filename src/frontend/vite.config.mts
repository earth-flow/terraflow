import react from "@vitejs/plugin-react-swc";
import * as dotenv from "dotenv";
import path from "path";
import { defineConfig, loadEnv } from "vite";
import istanbul from "vite-plugin-istanbul";
import svgr from "vite-plugin-svgr";
import tsconfigPaths from "vite-tsconfig-paths";
import {
  API_ROUTES,
  BASENAME,
  PORT,
  PROXY_TARGET,
} from "./src/customization/config-constants";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  const envTerraflowResult = dotenv.config({
    path: path.resolve(__dirname, "../../.env"),
  });

  const envTerraflow = envTerraflowResult.parsed || {};

  const apiRoutes = API_ROUTES || ["^/api/v1/", "^/api/v2/", "/health"];

  const target =
    env.VITE_PROXY_TARGET || PROXY_TARGET || "http://localhost:7860";

  const port = Number(env.VITE_PORT) || PORT || 3000;

  const proxyTargets = apiRoutes.reduce((proxyObj, route) => {
    proxyObj[route] = {
      target: target,
      changeOrigin: true,
      secure: false,
      ws: true,
    };
    return proxyObj;
  }, {});

  return {
    base: BASENAME || "",
    build: {
      outDir: "build",
    },
    define: {
      "import.meta.env.BACKEND_URL": JSON.stringify(
        envTerraflow.BACKEND_URL ?? "",
      ),
      "import.meta.env.ACCESS_TOKEN_EXPIRE_SECONDS": JSON.stringify(
        envTerraflow.ACCESS_TOKEN_EXPIRE_SECONDS ?? 60,
      ),
      "import.meta.env.CI": JSON.stringify(envTerraflow.CI ?? false),
      "import.meta.env.TERRAFLOW_AUTO_LOGIN": JSON.stringify(
        envTerraflow.TERRAFLOW_AUTO_LOGIN ?? true,
      ),
      "import.meta.env.TERRAFLOW_MCP_COMPOSER_ENABLED": JSON.stringify(
        envTerraflow.TERRAFLOW_MCP_COMPOSER_ENABLED ?? "true",
      ),
      // Compile-time hard kill switch for the palette Bundle-header
      // Reload action.  The actual user-facing gate is the runtime
      // ``enable_extension_reload`` flag served from ``/config`` (mirrors
      // ``TERRAFLOW_ENABLE_EXTENSION_RELOAD``), so a packaged frontend
      // built once can still light up the button when an operator opts
      // the backend in via ``--env-file`` or ``lfx extension dev``.
      // Default ``true`` here means the bundle SHIPS the UI; corporate
      // Mode B/C builds that want to drop the code entirely can set
      // ``TERRAFLOW_EXTENSION_RELOAD_ENABLED=false`` in ``.env`` to dead-code-
      // eliminate the Reload UI at build time.
      "import.meta.env.TERRAFLOW_EXTENSION_RELOAD_ENABLED": JSON.stringify(
        envTerraflow.TERRAFLOW_EXTENSION_RELOAD_ENABLED ?? "true",
      ),
      "import.meta.env.TERRAFLOW_WXO_UTM_SOURCE": JSON.stringify(
        envTerraflow.TERRAFLOW_WXO_UTM_SOURCE ?? "terraflow",
      ),
    },
    plugins: [
      react(),
      svgr(),
      tsconfigPaths(),
      istanbul({
        include: "src/**/*",
        extension: [".ts", ".tsx", ".js", ".jsx"],
        requireEnv: false,
      }),
    ],
    server: {
      port: port,
      proxy: {
        ...proxyTargets,
        "^/api/workspace/": {
          target: env.VITE_WATER_DEMO_API_TARGET || "http://127.0.0.1:7862",
          changeOrigin: true,
          secure: false,
        },
        "^/api/water-demo/": {
          target: env.VITE_WATER_DEMO_API_TARGET || "http://127.0.0.1:7862",
          changeOrigin: true,
          secure: false,
        },
      },
    },
  };
});
