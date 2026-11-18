import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, post } from "../lib/api";
import { NOUNS, ago, count, num } from "../lib/format";
import { CHAT_TYPE_LABEL } from "../lib/labels";
import { type DialogSyncStatus, useLive } from "../lib/live";
import { Alert, Button, Spinner, Toggle } from "../components/ui";

type ChatType = "channel" | "group" | "user" | "bot" | "saved";

const KINDS = [
  { key: "all", label: "الكل", types: null },
  { key: "channel", label: "القنوات", types: ["channel"] },
  { key: "group", label: "المجموعات", types: ["group"] },
  { key: "private", label: "المحادثات الخاصة والبوتات", types: ["user", "bot", "saved"] },
] as const;

interface ChatRow {
  id: number;
  title: string;
  username: string | null;
  type: ChatType;
  tracked: boolean;
  is_member: boolean;
  last_scan_at: string | null;
  last_scan_error: string | null;
  allowed_exts: string | null;
  max_size_mb: number | null;
  files: number;
  downloaded: number;
  pending: number;
  available: number;
  auto_download: boolean;
  scanning: boolean;
  queued: boolean;
}

export function ChatsPage() {
  const { revision, status } = useLive();
  const [chats, setChats] = useState<ChatRow[] | null>(null);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<(typeof KINDS)[number]["key"]>("all");
  const [error, setError] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  // The list comes from the local copy right away; the server syncs it from
  // Telegram in the background and reports progress in the live status.
  const [fetchedSync, setFetchedSync] = useState<DialogSyncStatus | null>(null);
  const sync = status?.dialogs ?? fetchedSync;

  const load = useCallback(async (refresh = false) => {
    try {
      const res = await api<{ chats: ChatRow[]; dialogs: DialogSyncStatus }>(
        `/api/chats${refresh ? "?refresh=true" : ""}`,
      );
      setChats(res.chats);
      setFetchedSync(res.dialogs);
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

  // A list fetched before the account was ready is empty: fetch again once it is.
  const accountReady = status?.account.state === "ready";
  useEffect(() => {
    if (accountReady) load();
  }, [accountReady]);

  // Show chats as each batch of the background sync is saved, and the final
  // list (left chats marked) when it finishes.
  const syncing = sync?.syncing ?? false;
  const syncLoaded = sync?.loaded ?? 0;
  useEffect(() => {
    if (chats) load();
  }, [syncing, syncLoaded]);

  const filtered = useMemo(() => {
    if (!chats) return [];
    const needle = fold(q.trim());
    const types: readonly string[] | null = KINDS.find((k) => k.key === kind)!.types;
    return chats.filter(
      (c) =>
        (!types || types.includes(c.type)) &&
        (!needle || fold(`${c.title} ${c.username ?? ""}`).includes(needle)),
    );
  }, [chats, q, kind]);

  const trackedCount = chats?.filter((c) => c.tracked).length ?? 0;

  async function refresh() {
    setRequesting(true);
    await load(true);
    setRequesting(false);
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
        فعّل متابعة محادثة ليفحصها البرنامج ويعرض ملفاتها في «المكتبة»، ثم يعيد فحصها تلقائياً لالتقاط الجديد.
        المتابعة لا تُنزّل شيئاً: أنت تختار ما يُنزَّل، أو تفعّل التنزيل التلقائي للمحادثة من «خيارات».
        ولقراءة رسائل أي محادثة اضغط «اقرأ الرسائل».
      </p>

      <div className="flex flex-wrap gap-3 items-center">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="ابحث باسم المحادثة"
          aria-label="بحث في المحادثات"
          className="flex-1 min-w-60 rounded-lg border border-line bg-white px-4 min-h-12 text-lg focus:border-binding focus:outline-none focus:ring-2 focus:ring-binding/25"
        />
        <Button variant="secondary" onClick={refresh} busy={requesting || syncing}>
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

      <div role="tablist" aria-label="نوع المحادثة" className="flex flex-wrap gap-2">
        {KINDS.map((k) => (
          <button
            key={k.key}
            role="tab"
            aria-selected={k.key === kind}
            onClick={() => setKind(k.key)}
            className={`rounded-full px-4 min-h-10 border transition-colors ${
              k.key === kind ? "bg-ink text-white border-ink" : "bg-white border-line hover:border-binding"
            }`}
          >
            {k.label}
          </button>
        ))}
      </div>

      {error && <Alert>{error}</Alert>}
      {!syncing && sync?.error && <Alert>{sync.error}</Alert>}

      {syncing && chats && chats.length > 0 && (
        <p role="status" className="flex items-center gap-3 text-ink-soft">
          <Spinner className="size-5" />
          جارٍ تحديث القائمة من تيليغرام…
          {syncLoaded > 0 && ` (وصلت ${num(syncLoaded)} حتى الآن)`}
        </p>
      )}

      {!chats ? (
        <div className="flex items-center gap-3 text-ink-soft">
          <Spinner className="size-5" /> جارٍ تحميل المحادثات…
        </div>
      ) : chats.length === 0 && syncing ? (
        <div className="flex items-center gap-3 text-ink-soft">
          <Spinner className="size-5" /> جارٍ جلب المحادثات من تيليغرام… قد يستغرق ذلك بضع دقائق في الحسابات الكبيرة.
        </div>
      ) : filtered.length === 0 && status && !accountReady ? (
        <div className="flex items-center gap-3 text-ink-soft">
          <Spinner className="size-5" /> حساب تيليغرام غير متصل بعد، ستظهر المحادثات عند اكتمال الاتصال…
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-ink-soft">
          {q ? "لا توجد محادثة بهذا الاسم." : "لا توجد محادثات من هذا النوع في حسابك."}
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
            <span className="text-sm text-ink-soft">{CHAT_TYPE_LABEL[c.type] ?? c.type}</span>
            {!c.is_member && <span className="text-sm text-danger">لم تعد عضواً فيها</span>}
          </div>
          {c.tracked && (
            <p className="mt-1 text-sm text-ink-soft">
              {c.files === 0 ? "لا توجد ملفات بعد" : count(c.files, NOUNS.file)}
              {c.downloaded > 0 && <> · نُزّل {count(c.downloaded, NOUNS.file)}</>}
              {c.available > 0 && (
                <>
                  {" · "}
                  <Link to={`/library?view=available&chat=${c.id}`} className="text-binding underline underline-offset-4">
                    {count(c.available, NOUNS.file)} متاحة للتنزيل
                  </Link>
                </>
              )}
              {c.pending > 0 && <> · {count(c.pending, NOUNS.file)} في الانتظار</>}
              {c.auto_download && <> · تنزيل تلقائي</>} · آخر فحص: {scanState ?? ago(c.last_scan_at)}
            </p>
          )}
          {c.tracked && c.last_scan_error && <p className="mt-1 text-sm text-danger">{c.last_scan_error}</p>}
        </div>
        <div className="flex flex-wrap gap-2 basis-full ps-18 sm:basis-auto sm:ps-0">
          {c.tracked && (
            <>
              <Button
                variant="secondary"
                disabled={c.scanning || c.queued}
                onClick={() => act(() => post(`/api/chats/${c.id}/scan`))}
              >
                {c.scanning ? <Spinner /> : null}
                افحص الآن
              </Button>
              {c.available > 0 && (
                <Button variant="secondary" onClick={() => act(() => post(`/api/chats/${c.id}/download-all`))}>
                  نزّل الكل ({num(c.available)})
                </Button>
              )}
              <Button variant="quiet" aria-expanded={open} onClick={() => setOpen(!open)}>
                خيارات
              </Button>
            </>
          )}
          {c.is_member && (
            <Link
              to={`/chats/${c.id}`}
              aria-label={`اقرأ رسائل ${c.title}`}
              className="inline-flex min-h-11 items-center rounded-lg px-4 font-semibold text-binding transition-colors hover:bg-binding-tint"
            >
              اقرأ الرسائل
            </Link>
          )}
        </div>
      </div>
      {open && c.tracked && <ChatOptions chat={c} onSaved={onChanged} />}
    </li>
  );
}

function ChatOptions({ chat, onSaved }: { chat: ChatRow; onSaved: () => Promise<void> }) {
  const [exts, setExts] = useState(chat.allowed_exts ?? "");
  const [maxMb, setMaxMb] = useState(chat.max_size_mb?.toString() ?? "");
  const [auto, setAuto] = useState(chat.auto_download);
  const [saved, setSaved] = useState(false);

  async function save() {
    await post(`/api/chats/${chat.id}/settings`, {
      allowed_exts: exts.trim() || null,
      max_size_mb: maxMb.trim() === "" ? null : Number(maxMb),
      auto_download: auto,
    });
    setSaved(true);
    await onSaved();
  }

  return (
    <div className="mt-4 ms-18 rounded-lg bg-paper p-4 space-y-3">
      <label className="flex items-start gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={auto}
          onChange={(e) => {
            setAuto(e.target.checked);
            setSaved(false);
          }}
          className="mt-1 size-5 accent-binding"
        />
        <span>
          <span className="block font-semibold">نزّل الملفات الجديدة تلقائياً</span>
          <span className="block text-sm text-ink-soft">
            كل ملف جديد يظهر في هذه المحادثة يُنزَّل دون أن تختاره. الملفات الموجودة سابقاً لا تتأثر.
          </span>
        </span>
      </label>
      <p className="text-sm text-ink-soft">اترك الحقلين التاليين فارغين لاستخدام الإعداد العام.</p>
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
