export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type Listener = () => void;
const unauthListeners = new Set<Listener>();

/** Called whenever the server says the app session has expired. */
export function onUnauthenticated(fn: Listener): () => void {
  unauthListeners.add(fn);
  return () => unauthListeners.delete(fn);
}

export async function api<T = unknown>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: options.method ?? (options.body !== undefined ? "POST" : "GET"),
      headers: options.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      credentials: "same-origin",
      signal: options.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network", "تعذّر الاتصال بالخادم. تأكد أن البرنامج يعمل.");
  }
  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
  let code = "error";
  let message = `حدث خطأ (${res.status})`;
  try {
    const data = await res.json();
    const d = data?.detail;
    if (d && typeof d === "object" && "message" in d) {
      code = d.code;
      message = d.message;
    } else if (typeof d === "string") {
      message = d;
    }
  } catch {
    /* non-JSON error body */
  }
  if (res.status === 401 && code === "unauthenticated") {
    unauthListeners.forEach((fn) => fn());
  }
  throw new ApiError(res.status, code, message);
}

export const post = <T = unknown>(path: string, body: unknown = {}) => api<T>(path, { body });
