import { type ReactNode, createContext, createElement, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { AccountStatus } from "./account";

export type EngineStateName =
  | "running"
  | "idle"
  | "paused"
  | "flood_wait"
  | "disk_full"
  | "offline"
  | "scheduled"
  | "waiting_telegram";

export interface ActiveDownload {
  id: number;
  name: string;
  size: number;
  done: number;
  speed: number;
}

export interface Stats {
  files: number;
  library_bytes: number;
  documents: number;
  status_counts: Record<"available" | "pending" | "downloading" | "done" | "failed" | "skipped" | "duplicate", number>;
  duplicates: number;
  space_saved_bytes: number;
  bandwidth_saved_bytes: number;
  repeat_posts: number;
  remaining_bytes: number;
}

export interface LiveStatus {
  engine: {
    state: EngineStateName;
    paused: boolean;
    flood_wait_seconds: number;
    disk_full: string | null;
    workers: number;
    speed_limit_kbps: number;
    schedule: { enabled: boolean; start: string; end: string };
    active: ActiveDownload[];
  };
  scan: {
    current: {
      chat_id: number;
      title: string;
      messages: number;
      new_documents: number;
      flood_wait_seconds: number;
    } | null;
    queued: number;
  };
  account: AccountStatus;
  dialogs: DialogSyncStatus;
  stats: Stats;
}

/** Background sync of the chat list from Telegram. */
export interface DialogSyncStatus {
  syncing: boolean;
  /** A full sync has finished since the app started. */
  synced: boolean;
  /** Chats received so far by the running (or last) sync. */
  loaded: number;
  error: string | null;
}

export interface Live {
  status: LiveStatus | null;
  connected: boolean;
  /** Increments whenever documents change state; pages can refetch on it. */
  revision: number;
}

// Two contexts, because they change at different paces: the status every
// second while something downloads, the revision when a file changes state.
// A component re-renders only for the one it reads.
const StatusContext = createContext<Pick<Live, "status" | "connected">>({ status: null, connected: false });
const RevisionContext = createContext(0);

/** The live status (and the revision): re-renders with every snapshot. */
export const useLive = (): Live => ({ ...useContext(StatusContext), revision: useContext(RevisionContext) });

/** Only the revision: re-renders when documents changed state, not every second. */
export const useRevision = () => useContext(RevisionContext);

/** A burst of changes refetches once, this long after it starts... */
const REFETCH_AFTER_MS = 1500;
/** ...and while changes keep coming (a queue of small files), this often. */
const REFETCH_EVERY_MS = 5000;

/** One EventSource for the whole app; the browser reconnects on its own.
 * The state lives here, not in the app's root, so a new snapshot re-renders
 * the components that read it and nothing else. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const es = new EventSource("/api/events");
    let pending: number | null = null;
    let bumpedAt = 0;
    // Coalesce bursts of document/scan events into one refetch signal.
    const bump = () => {
      if (pending !== null) return;
      const wait = Math.max(REFETCH_AFTER_MS, bumpedAt + REFETCH_EVERY_MS - Date.now());
      pending = window.setTimeout(() => {
        pending = null;
        bumpedAt = Date.now();
        setRevision((r) => r + 1);
      }, wait);
    };
    // A snapshot arrives every second and is mostly the one before it:
    // the same text changes nothing, so nothing is rendered for it.
    let shown: string | null = null;
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("status", (e) => {
      setConnected(true);
      const data = (e as MessageEvent).data as string;
      if (data === shown) return;
      shown = data;
      setStatus(JSON.parse(data));
    });
    // Chat-list sync progress, sooner than the next status snapshot.
    es.addEventListener("dialogs", (e) => {
      const dialogs = JSON.parse((e as MessageEvent).data) as DialogSyncStatus;
      shown = null;
      setStatus((s) => (s ? { ...s, dialogs } : s));
    });
    es.addEventListener("document", bump);
    es.addEventListener("scan_done", bump);
    return () => {
      es.close();
      if (pending !== null) window.clearTimeout(pending);
    };
  }, []);

  const value = useMemo(() => ({ status, connected }), [status, connected]);
  return createElement(
    StatusContext.Provider,
    { value },
    createElement(RevisionContext.Provider, { value: revision }, children),
  );
}

/** `value`, but changing at most once every `ms`: for a refetch that follows
 * something that can change many times a second. The last value always arrives. */
export function useThrottled<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value);
  const last = useRef(0);
  useEffect(() => {
    const show = () => {
      last.current = Date.now();
      setShown(value);
    };
    const wait = last.current + ms - Date.now();
    if (wait <= 0) {
      show();
      return;
    }
    const timer = window.setTimeout(show, wait);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return shown;
}
