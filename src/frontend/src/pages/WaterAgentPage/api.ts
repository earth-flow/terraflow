// Terraflow's global fetch interceptor writes into `config.headers`, so every
// request must pass an init object with a plain headers record.
export function apiFetch(path: string, init: RequestInit = {}) {
  return fetch(path, {
    ...init,
    headers: { ...(init.headers as Record<string, string> | undefined) },
  });
}
