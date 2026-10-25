import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, api, post } from "../lib/api";
import { NOUNS, bytes, count, dateTime, num } from "../lib/format";
import { STATUS_LABEL, reasonText } from "../lib/labels";
import { useLive } from "../lib/live";
import { Alert, Button, ProgressBar, Spinner } from "../components/ui";

interface Item {
  id: number;
  name: string;
  ext: string | null;
  size: number;
  status: string;
  priority: number;
  bytes_downloaded: number;
  attempts: number;
  last_error: string | null;
  next_attempt_at: string | null;
  chats: string[];
}

const TABS = [
  { key: "waiting", label: "في الانتظار", statuses: ["downloading", "pending"] },
  { key: "failed", label: "لم ينجح تنزيلها", statuses: ["failed"] },
  { key: "skipped", label: "متخطاة", statuses: ["skipped"] },
] as const;

const PAGE = 50;

export function QueuePage() {
  const { revision, status } = useLive();
  const [sp, setSp] = useSearchParams();
  const tab = TABS.find((t) => t.key === sp.get("tab")) ?? TABS[0];
  const [items, setItems] = useState<Item[] | null>(null);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(PAGE);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const qs = new URLSearchParams({ page_size: String(Math.min(limit, 200)) });
    tab.statuses.forEach((s) => qs.append("status", s));
    try {
      const r = await api<{ items: Item[]; total: number }>(`/api/documents?${qs}`);
      setItems(r.items);
      setTotal(r.total);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر تحميل القائمة.");
    }
  }, [tab, limit]);

  useEffect(() => {
    load();
  }, [load, revision]);

  // Keep the waiting list fresh while downloads move through it.
  const activeKey = status?.engine.active.map((a) => a.id).join(",");
  useEffect(() => {
    if (tab.key === "waiting") load();
  }, [activeKey]);

  async function act(path: string) {
    setError(null);
    try {
      await post(path);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "حدث خطأ.");
    }
  }

  const counts = status?.stats.status_counts;
  const tabCount = (key: string) =>
    !counts ? null : key === "waiting" ? counts.pending + counts.downloading : key === "failed" ? counts.failed : counts.skipped;
  const progress = new Map(status?.engine.active.map((a) => [a.id, a]) ?? []);

  return (
    <div className="space-y-6">
      <h1 className="page-title">قائمة الانتظار والأخطاء</h1>

      <div role="tablist" className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={t.key === tab.key}
            onClick={() => {
              setSp(t.key === "waiting" ? {} : { tab: t.key });
              setItems(null);
              setLimit(PAGE);
            }}
            className={`rounded-lg px-4 min-h-11 font-semibold border transition-colors ${
              t.key === tab.key ? "bg-ink text-white border-ink" : "bg-white border-line hover:border-binding"
            }`}
          >
            {t.label}
            {tabCount(t.key) !== null && <span className="ms-2 opacity-80">{num(tabCount(t.key)!)}</span>}
          </button>
        ))}
      </div>

      {tab.key === "failed" && total > 0 && (
        <div className="flex flex-wrap items-center gap-4">
          <p className="text-ink-soft">بعد عدة محاولات فاشلة يتوقف البرنامج عن المحاولة حتى تطلب ذلك.</p>
          <Button onClick={() => act("/api/documents/retry-failed")}>أعد محاولة الكل</Button>
        </div>
      )}
      {tab.key === "skipped" && (
        <p className="text-ink-soft max-w-2xl">
          ملفات لم تُنزَّل لأن نوعها أو حجمها غير مسموح في الإعدادات، أو لأنك ألغيتها. يمكنك تنزيل أي منها
          يدوياً.
        </p>
      )}

      {error && <Alert>{error}</Alert>}

      {!items ? (
        <div className="flex items-center gap-3 text-ink-soft">
          <Spinner className="size-5" /> جارٍ التحميل…
        </div>
      ) : items.length === 0 ? (
        <p className="text-ink-soft text-lg">
          {tab.key === "waiting" ? "لا شيء في الانتظار." : tab.key === "failed" ? "لا توجد أخطاء." : "لا توجد ملفات متخطاة."}
        </p>
      ) : (
        <>
          <ul className="rounded-xl border border-line bg-white divide-y divide-line">
            {items.map((it) => {
              const live = progress.get(it.id);
              const done = live?.done ?? it.bytes_downloaded;
              const reason = reasonText(it.last_error);
              return (
                <li key={it.id} className="px-5 py-4">
                  <div className="flex flex-wrap items-start gap-4">
                    <div className="flex-1 min-w-0">
                      <bdi className="block font-semibold break-words">{it.name}</bdi>
                      <p className="mt-1 text-sm text-ink-soft">
                        {STATUS_LABEL[it.status]} · {bytes(it.size)}
                        {it.chats.length > 0 && <> · من: {it.chats.join("، ")}</>}
                        {it.status === "pending" && it.next_attempt_at && (
                          <> · محاولة جديدة {dateTime(it.next_attempt_at)}</>
                        )}
                      </p>
                      {reason && (
                        <p className={`mt-1 text-sm ${it.status === "failed" ? "text-danger" : "text-ink-soft"}`}>
                          {it.status === "pending" && it.attempts > 0 && "آخر خطأ: "}
                          {reason}
                          {it.status === "failed" && it.attempts > 1 && <> (بعد {count(it.attempts, NOUNS.attempt)})</>}
                        </p>
                      )}
                      {(it.status === "downloading" || done > 0) && it.status !== "skipped" && (
                        <div className="mt-3 max-w-xl">
                          <ProgressBar value={done} max={it.size} />
                        </div>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2 basis-full sm:basis-auto">
                      {it.status === "pending" && (
                        <Button variant="secondary" onClick={() => act(`/api/documents/${it.id}/priority`)}>
                          نزّله أولاً
                        </Button>
                      )}
                      {(it.status === "failed" || it.status === "skipped") && (
                        <Button onClick={() => act(`/api/documents/${it.id}/retry`)}>
                          {it.status === "failed" ? "أعد المحاولة" : "نزّله"}
                        </Button>
                      )}
                      {it.status !== "skipped" && (
                        <Button variant="danger" onClick={() => act(`/api/documents/${it.id}/cancel`)}>
                          إلغاء
                        </Button>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
          {items.length < total && limit < 200 && (
            <div className="text-center">
              <Button variant="secondary" onClick={() => setLimit(limit + PAGE)}>
                عرض المزيد ({num(total - items.length)} أخرى)
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
