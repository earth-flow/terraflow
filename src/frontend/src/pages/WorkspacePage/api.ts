import { useCallback, useEffect, useState } from "react";
import type {
  CatalogCollection,
  CatalogItem,
  Collection,
  Scene,
} from "./types";

export const API = "/api/workspace";

/** Payload of one server-sent event; callers narrow it by event name. */
export type StreamData = { text?: string } & Record<string, unknown>;

// Terraflow's global fetch interceptor writes into `config.headers`, so every
// request must pass an init object with a plain headers record.
export function apiFetch(path: string, init: RequestInit = {}) {
  return fetch(path, {
    ...init,
    headers: { ...(init.headers as Record<string, string> | undefined) },
  });
}

export async function apiJson<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await apiFetch(path, init);
  const body = (await response.json()) as T & { error?: string };
  if (!response.ok)
    throw new Error(body.error || "请求失败（HTTP " + response.status + "）");
  return body;
}

export function postJson(path: string, payload: unknown) {
  return apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Read a server-sent-event response body and hand each event to `onEvent`. */
export async function readEvents(
  response: Response,
  onEvent: (event: string, data: StreamData) => void,
) {
  if (!response.ok || !response.body) {
    let message = "请求失败（HTTP " + response.status + "）";
    try {
      message =
        ((await response.json()) as { error?: string }).error || message;
    } catch {
      // not JSON
    }
    throw new Error(message);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }
      if (data.length) onEvent(event, JSON.parse(data.join("\n")));
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export function useScenes() {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    try {
      const body = await apiJson<{
        scenes: Scene[];
        collections: Collection[];
      }>(API + "/scenes");
      setScenes(body.scenes);
      setCollections(body.collections);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取数据列表。");
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  return { scenes, collections, error, refresh };
}

/** The data catalog: every dataset as a STAC Item, grouped by collection. */
export function useCatalog() {
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [collections, setCollections] = useState<CatalogCollection[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try {
      const body = await apiJson<{
        items: CatalogItem[];
        collections: CatalogCollection[];
      }>(API + "/catalog");
      setItems(body.items);
      setCollections(body.collections);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取数据列表。");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  return { items, collections, error, loading, refresh };
}

/** Scenes a user picks from: no self-check samples, developer data only on request. */
export function pickableScenes(scenes: Scene[], showDeveloper: boolean) {
  return scenes.filter(
    (scene) =>
      !scene.self_check_for &&
      (showDeveloper || scene.audience !== "developer"),
  );
}

export function sceneFacts(scene: Scene) {
  return [
    scene.acquired,
    scene.sensor,
    scene.kind_label,
    scene.area_km2 ? "约 " + scene.area_km2.toFixed(0) + " km²" : null,
  ].filter(Boolean) as string[];
}

export function displayTool(slug: string) {
  return slug.split(".").at(-1) ?? slug;
}

export function formatJson(value: unknown) {
  return value === undefined || value === null
    ? "—"
    : JSON.stringify(value, null, 2);
}
