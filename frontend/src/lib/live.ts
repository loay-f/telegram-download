import { createContext, useContext, useEffect, useRef, useState } from "react";
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

export const LiveContext = createContext<Live>({ status: null, connected: false, revision: 0 });

export const useLive = () => useContext(LiveContext);

/** One EventSource for the whole app; the browser reconnects on its own. */
export function useLiveConnection(enabled: boolean): Live {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [revision, setRevision] = useState(0);
  const pending = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const es = new EventSource("/api/events");
    // Coalesce bursts of document/scan events into one refetch signal.
    const bump = () => {
      if (pending.current !== null) return;
      pending.current = window.setTimeout(() => {
        pending.current = null;
        setRevision((r) => r + 1);
      }, 1500);
    };
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("status", (e) => {
      setConnected(true);
      setStatus(JSON.parse((e as MessageEvent).data));
    });
    // Chat-list sync progress, sooner than the next status snapshot.
    es.addEventListener("dialogs", (e) => {
      const dialogs = JSON.parse((e as MessageEvent).data) as DialogSyncStatus;
      setStatus((s) => (s ? { ...s, dialogs } : s));
    });
    es.addEventListener("document", bump);
    es.addEventListener("scan_done", bump);
    return () => {
      es.close();
      if (pending.current !== null) window.clearTimeout(pending.current);
    };
  }, [enabled]);

  return { status, connected, revision };
}
