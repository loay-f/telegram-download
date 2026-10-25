import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api, post } from "../lib/api";
import { NOUNS, ago, count } from "../lib/format";
import { useLive } from "../lib/live";
import { Alert, Button, Spinner, Toggle } from "../components/ui";

interface ChatRow {
  id: number;
  title: string;
  username: string | null;
  type: "channel" | "group";
  tracked: boolean;
  is_member: boolean;
  last_scan_at: string | null;
  last_scan_error: string | null;
  allowed_exts: string | null;
  max_size_mb: number | null;
  files: number;
  downloaded: number;
  pending: number;
  scanning: boolean;
  queued: boolean;
}

export function ChatsPage() {
  const { revision, status } = useLive();
  const [chats, setChats] = useState<ChatRow[] | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (refresh = false) => {
    try {
      const res = await api<{ chats: ChatRow[] }>(`/api/chats${refresh ? "?refresh=true" : ""}`);
      setChats(res.chats);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر تحميل المحادثات.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, revision]);

  // Reflect scan start/finish without waiting for a document event.
  const scanningId = status?.scan.current?.chat_id ?? null;
  useEffect(() => {
    if (chats) load();
  }, [scanningId]);

  const filtered = useMemo(() => {
    if (!chats) return [];
    const needle = fold(q.trim());
    return needle ? chats.filter((c) => fold(`${c.title} ${c.username ?? ""}`).includes(needle)) : chats;
  }, [chats, q]);

  const trackedCount = chats?.filter((c) => c.tracked).length ?? 0;

  async function refresh() {
    setRefreshing(true);
    await load(true);
    setRefreshing(false);
  }

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "حدث خطأ.");
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="page-title">المحادثات</h1>
      <p className="text-ink-soft max-w-2xl">
        فعّل متابعة القنوات والمجموعات التي تريد تنزيل كتبها. يفحصها البرنامج الآن ثم يعيد فحصها تلقائياً
        لالتقاط الكتب الجديدة.
      </p>

      <div className="flex flex-wrap gap-3 items-center">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="ابحث باسم القناة أو المجموعة"
          aria-label="بحث في المحادثات"
          className="flex-1 min-w-60 rounded-lg border border-line bg-white px-4 min-h-12 text-lg focus:border-binding focus:outline-none focus:ring-2 focus:ring-binding/25"
        />
        <Button variant="secondary" onClick={refresh} busy={refreshing}>
          تحديث القائمة من تيليغرام
        </Button>
        <Button
          variant="secondary"
          disabled={trackedCount === 0}
          onClick={() => act(() => post("/api/chats/scan-all"))}
        >
          افحص كل المتابَعة الآن
        </Button>
      </div>

      {error && <Alert>{error}</Alert>}

      {!chats ? (
        <div className="flex items-center gap-3 text-ink-soft">
          <Spinner className="size-5" /> جارٍ جلب المحادثات من تيليغرام…
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-ink-soft">
          {q ? "لا توجد محادثة بهذا الاسم." : "لم يُعثر على قنوات أو مجموعات في حسابك."}
        </p>
      ) : (
        <ul className="rounded-xl border border-line bg-white divide-y divide-line">
          {filtered.map((c) => (
            <ChatItem key={c.id} chat={c} onChanged={load} act={act} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ChatItem({ chat: c, onChanged, act }: {
  chat: ChatRow;
  onChanged: () => Promise<void>;
  act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const scanState = c.scanning ? "يُفحص الآن…" : c.queued ? "بانتظار الفحص" : null;

  return (
    <li className={`px-5 py-4 ${c.tracked ? "" : "text-ink-soft"}`}>
      <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
        <Toggle
          checked={c.tracked}
          label={`متابعة ${c.title}`}
          onChange={(v) => act(() => post(`/api/chats/${c.id}/track`, { tracked: v }))}
        />
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-3">
            <bdi className={`text-lg ${c.tracked ? "font-semibold text-ink" : ""}`}>{c.title}</bdi>
            <span className="text-sm text-ink-soft">{c.type === "channel" ? "قناة" : "مجموعة"}</span>
            {!c.is_member && <span className="text-sm text-danger">لم تعد عضواً فيها</span>}
          </div>
          {c.tracked && (
            <p className="mt-1 text-sm text-ink-soft">
              {count(c.files, NOUNS.file)} · نُزّل {count(c.downloaded, NOUNS.file)}
              {c.pending > 0 && <> · {count(c.pending, NOUNS.file)} في الانتظار</>} · آخر فحص:{" "}
              {scanState ?? ago(c.last_scan_at)}
            </p>
          )}
          {c.tracked && c.last_scan_error && <p className="mt-1 text-sm text-danger">{c.last_scan_error}</p>}
        </div>
        {c.tracked && (
          <div className="flex flex-wrap gap-2 basis-full ps-18 sm:basis-auto sm:ps-0">
            <Button
              variant="secondary"
              disabled={c.scanning || c.queued}
              onClick={() => act(() => post(`/api/chats/${c.id}/scan`))}
            >
              {c.scanning ? <Spinner /> : null}
              افحص الآن
            </Button>
            <Button variant="quiet" aria-expanded={open} onClick={() => setOpen(!open)}>
              خيارات
            </Button>
          </div>
        )}
      </div>
      {open && c.tracked && <ChatOptions chat={c} onSaved={onChanged} />}
    </li>
  );
}

function ChatOptions({ chat, onSaved }: { chat: ChatRow; onSaved: () => Promise<void> }) {
  const [exts, setExts] = useState(chat.allowed_exts ?? "");
  const [maxMb, setMaxMb] = useState(chat.max_size_mb?.toString() ?? "");
  const [saved, setSaved] = useState(false);

  async function save() {
    await post(`/api/chats/${chat.id}/settings`, {
      allowed_exts: exts.trim() || null,
      max_size_mb: maxMb.trim() === "" ? null : Number(maxMb),
    });
    setSaved(true);
    await onSaved();
  }

  return (
    <div className="mt-4 ms-18 rounded-lg bg-paper p-4 space-y-3">
      <p className="text-sm text-ink-soft">اترك الحقل فارغاً لاستخدام الإعداد العام.</p>
      <label className="block">
        <span className="block font-semibold mb-1">أنواع الملفات لهذه المحادثة</span>
        <input
          dir="ltr"
          value={exts}
          onChange={(e) => {
            setExts(e.target.value);
            setSaved(false);
          }}
          placeholder="pdf, epub"
          className="w-full rounded-lg border border-line bg-white px-3 min-h-11 text-start"
        />
      </label>
      <label className="block">
        <span className="block font-semibold mb-1">أقصى حجم للملف (ميغابايت)</span>
        <input
          type="number"
          min={0}
          value={maxMb}
          onChange={(e) => {
            setMaxMb(e.target.value);
            setSaved(false);
          }}
          className="w-40 rounded-lg border border-line bg-white px-3 min-h-11"
        />
      </label>
      <div className="flex items-center gap-3">
        <Button onClick={save}>حفظ الخيارات</Button>
        {saved && <span className="text-binding">حُفظت الخيارات. تُطبَّق على الملفات الجديدة.</span>}
      </div>
    </div>
  );
}

function fold(s: string): string {
  return s
    .toLowerCase()
    .replace(/[ً-ٰٟـ]/g, "")
    .replace(/[أإآٱ]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ة/g, "ه");
}
