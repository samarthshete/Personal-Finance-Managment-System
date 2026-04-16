const BASE = process.env.NEXT_PUBLIC_API_BASE_URL;

if (!BASE) {
  throw new Error("NEXT_PUBLIC_API_BASE_URL is required");
}

export interface ApiError {
  status: number;
  detail: string;
  raw?: unknown;
}

let isRefreshing = false;

function forceLogout() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  opts: {
    method?: string;
    headers?: Record<string, string>;
    body?: unknown;
    auth?: boolean;
    formData?: FormData;
    formUrlEncoded?: URLSearchParams;
  } = {}
): Promise<T> {
  const { method = "GET", headers = {}, body, auth = true, formData, formUrlEncoded } = opts;

  const h: Record<string, string> = { ...headers };

  if (auth) {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    if (token) h["Authorization"] = `Bearer ${token}`;
  }

  let reqBody: BodyInit | undefined;
  if (formData) {
    reqBody = formData;
  } else if (formUrlEncoded) {
    reqBody = formUrlEncoded;
    h["Content-Type"] = "application/x-www-form-urlencoded";
  } else if (body !== undefined) {
    reqBody = JSON.stringify(body);
    h["Content-Type"] = "application/json";
  }

  const res = await fetch(`${BASE}${path}`, { method, headers: h, body: reqBody });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    let raw: unknown;
    try {
      raw = await res.json();
      const d = (raw as Record<string, unknown>).detail;
      if (typeof d === "string") detail = d;
      else if (d && typeof d === "object") {
        detail = (d as Record<string, string>).detail || JSON.stringify(d);
      }
    } catch {
      try { detail = await res.text(); } catch { /* noop */ }
    }

    if (res.status === 401 && auth && typeof window !== "undefined") {
      const refresh = localStorage.getItem("refresh_token");
      if (refresh && !isRefreshing) {
        isRefreshing = true;
        try {
          const rr = await fetch(`${BASE}/api/v1/auth/refresh`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ refresh_token: refresh }),
          });
          if (!rr.ok) {
            forceLogout();
            throw { status: rr.status, detail: "Session expired", raw: null } as ApiError;
          }
          const data = await rr.json();
          localStorage.setItem("access_token", data.access_token);
          if (data.refresh_token) localStorage.setItem("refresh_token", data.refresh_token);
          h["Authorization"] = `Bearer ${data.access_token}`;

          const retry = await fetch(`${BASE}${path}`, { method, headers: h, body: reqBody });
          if (retry.ok) {
            if (retry.status === 204) return undefined as T;
            return retry.json();
          }
          forceLogout();
          throw { status: retry.status, detail: "Session expired after refresh", raw: null } as ApiError;
        } catch (err) {
          if ((err as ApiError).status) throw err;
          forceLogout();
          throw { status: 401, detail: "Session expired", raw: null } as ApiError;
        } finally {
          isRefreshing = false;
        }
      }
      if (!refresh) {
        forceLogout();
      }
    }

    throw { status: res.status, detail, raw } as ApiError;
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}
