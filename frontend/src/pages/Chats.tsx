import { type ReactNode, memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, post } from "../lib/api";
import { NOUNS, ago, count, date, num } from "../lib/format";
import { CHAT_TYPE_LABEL, messagePreview } from "../lib/labels";
import { type DialogSyncStatus, useLive } from "../lib/live";
import { FileHitRow, type Hit, downloadHit, sameHit } from "../components/FileHit";
import { FormatPicker, SizeLimit } from "../components/FormatPicker";
import { FILE_KINDS } from "../lib/fileTypes";
import { Highlight } from "../components/Highlight";
import { SearchIcon } from "../components/icons";
import { Alert, Avatar, Button, Spinner, Toggle } from "../components/ui";

type ChatType = "channel" | "group" | "user" | "bot" | "saved";

const KINDS = [
  { key: "all", label: "الكل", types: null },
  { key: "channel", label: "القنوات", types: ["channel"] },
  { key: "group", label: "المجموعات", types: ["group"] },
  { key: "private", label: "المحادثات الخاصة والبوتات", types: ["user", "bot", "saved"] },
] as const;

/** A message found by searching all chats. */
interface MessageHit {
  chat: { id: number; title: string; type: string };
  id: number;
  date: string | null;
  text: string;
  out: boolean;
  sender: string | null;
  media: { kind: string; name: string | null } | null;
}

/** What Telegram found for `q`; a part is null when its search failed. */
interface Found {
  q: string;
  messages: MessageHit[] | null;
  moreMessages: boolean;
  files: Hit[] | null;
  error: string | null;
}

/** Telegram is asked this long after typing stops. */
const SEARCH_AFTER_MS = 600;
/** Results shown per section before "show all". */
const SHOWN = 5;

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

  // Typing a name filters the list at once; the same words are also looked
  // for in the messages and files of every chat, by Telegram.
  const query = q.trim().replace(/\s+/g, " ");
  const [found, setFound] = useState<Found | null>(null);
  const [finding, setFinding] = useState(false);
  const run = useRef(0); // only the latest search may show its answer
  useEffect(() => {
    const mine = ++run.current;
    if (query.length < 2) {
      setFound(null);
      setFinding(false);
      return;
    }
    setFinding(true);
    const timer = window.setTimeout(async () => {
      const params = new URLSearchParams({ q: query });
      const [messages, files] = await Promise.allSettled([
        // The most Telegram gives at once: messages that are files are left
        // out of these (they are in the files), which can be most of them.
        api<{ items: MessageHit[]; has_more: boolean }>(`/api/search/messages?${params}&limit=100`),
        api<{ items: Hit[] }>(`/api/search?${params}`),
      ]);
      if (mine !== run.current) return;
      const failed = messages.status === "rejected" ? messages.reason : files.status === "rejected" ? files.reason : null;
      setFound({
        q: query,
        messages: messages.status === "fulfilled" ? messages.value.items : null,
        moreMessages: messages.status === "fulfilled" && messages.value.has_more,
        files: files.status === "fulfilled" ? files.value.items : null,
        error: failed === null ? null : failed instanceof ApiError ? failed.message : "تعذّر البحث في تيليغرام.",
      });
      setFinding(false);
    }, SEARCH_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [query]);

  const kindTypes: readonly string[] | null = KINDS.find((k) => k.key === kind)!.types;
  const ofKind = (type: string) => !kindTypes || kindTypes.includes(type);
  const foundMessages = found?.messages?.filter((m) => ofKind(m.chat.type)) ?? null;
  const foundFiles = found?.files?.filter((f) => ofKind(f.chat.type)) ?? null;

  async function pickFile(hit: Hit) {
    try {
      const updated = await downloadHit(hit);
      setFound((now) => now && { ...now, files: now.files?.map((f) => (sameHit(f, hit) ? updated : f)) ?? null });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر إضافة الملف.");
    }
  }

  async function refresh() {
    setRequesting(true);
    await load(true);
    setRequesting(false);
  }

  const act = useCallback(
    async (fn: () => Promise<unknown>) => {
      try {
        await fn();
        await load();
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "حدث خطأ.");
      }
    },
    [load],
  );

  return (
    <div className="space-y-6">
      <h1 className="page-title">المحادثات</h1>
      <p className="text-ink-soft max-w-2xl">
        فعّل متابعة محادثة ليفحصها البرنامج ويعرض ملفاتها في «المكتبة»، ثم يعيد فحصها تلقائياً لالتقاط الجديد.
        المتابعة لا تُنزّل شيئاً: أنت تختار ما يُنزَّل، أو تفعّل التنزيل التلقائي للمحادثة من «خيارات».
        ولقراءة رسائل أي محادثة اضغط «اقرأ الرسائل». والبحث يجد المحادثات بأسمائها، والرسائل والملفات في كل
        محادثاتك.
      </p>

      <div className="flex flex-wrap gap-3 items-center">
        <div className="relative min-w-60 flex-1">
          <SearchIcon className="pointer-events-none absolute start-4 top-1/2 size-5 -translate-y-1/2 text-ink-soft" />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="ابحث في المحادثات والرسائل والملفات"
            aria-label="ابحث في المحادثات والرسائل والملفات"
            className="min-h-12 w-full rounded-full border border-line bg-white pe-4 ps-12 text-lg focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25"
          />
        </div>
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
              k.key === kind ? "bg-accent text-white border-accent" : "bg-white border-line hover:border-accent"
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
      ) : query ? (
        <div className="space-y-7">
          <Results
            key={`chats ${query}`}
            title="المحادثات"
            items={filtered}
            empty="لا توجد محادثة بهذا الاسم."
            render={(c) => <ChatItem key={c.id} chat={c} onChanged={load} act={act} />}
          />
          {query.length < 2 ? (
            <p className="text-ink-soft">اكتب حرفين أو أكثر للبحث في الرسائل والملفات أيضاً.</p>
          ) : (
            <>
              {found?.error && <Alert>{found.error}</Alert>}
              <Results
                key={`messages ${found?.q}`}
                title="الرسائل"
                items={foundMessages}
                more={found?.moreMessages}
                busy={finding}
                empty={found?.messages ? "لا توجد رسائل مطابقة." : "تعذّر البحث في الرسائل."}
                render={(m) => <MessageHitRow key={`${m.chat.id}:${m.id}`} hit={m} query={found!.q} />}
              />
              <Results
                key={`files ${found?.q}`}
                title="الملفات"
                items={foundFiles}
                busy={finding}
                empty={found?.files ? "لا توجد ملفات مطابقة." : "تعذّر البحث في الملفات."}
                render={(f) => <FileHitRow key={`${f.chat.id}:${f.msg_id}`} hit={f} onPick={() => pickFile(f)} />}
              />
            </>
          )}
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-ink-soft">لا توجد محادثات من هذا النوع في حسابك.</p>
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

/** One kind of search result: a heading with its count, the first few
 * results, and a way to see the rest. `items` is null while the first answer
 * is on its way (or when it could not be had). */
function Results<T>({ title, items, more, busy, empty, render }: {
  title: string;
  items: T[] | null;
  /** There are more results than were fetched. */
  more?: boolean;
  busy?: boolean;
  empty: string;
  render: (item: T) => ReactNode;
}) {
  const [all, setAll] = useState(false);
  return (
    <section aria-label={title}>
      <h2 className="mb-2 flex items-center gap-2 px-1 font-semibold text-accent-strong">
        {title}
        {items !== null && (
          <span className="rounded-full bg-accent-tint px-2 text-sm">
            {num(items.length)}
            {more && items.length > 0 && "+"}
          </span>
        )}
        {busy && <Spinner />}
      </h2>
      {items === null ? (
        <p className="px-1 text-ink-soft">{busy ? "يبحث في تيليغرام…" : empty}</p>
      ) : items.length === 0 ? (
        <p className="px-1 text-ink-soft">{empty}</p>
      ) : (
        <>
          <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-white">
            {(all ? items : items.slice(0, SHOWN)).map(render)}
          </ul>
          {items.length > SHOWN && (
            <Button variant="quiet" className="mt-1" aria-expanded={all} onClick={() => setAll(!all)}>
              {all ? "عرض أقل" : `عرض الكل (${num(items.length)}${more ? "+" : ""})`}
            </Button>
          )}
        </>
      )}
    </section>
  );
}

/** A found message; opens its chat at that message. */
function MessageHitRow({ hit: h, query }: { hit: MessageHit; query: string }) {
  const type = CHAT_TYPE_LABEL[h.chat.type];
  const who = h.out ? "أنت" : h.sender;
  return (
    <li>
      <Link
        to={`/chats/${h.chat.id}?m=${h.id}`}
        className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-canvas sm:gap-4 sm:px-5"
      >
        <Avatar id={h.chat.id} name={h.chat.title} />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-3">
            <span className="min-w-0 truncate">
              <bdi className="font-semibold">{h.chat.title}</bdi>
              {type && <span className="ms-2 text-sm text-ink-soft">{type}</span>}
            </span>
            <span className="shrink-0 text-xs text-ink-soft">{date(h.date)}</span>
          </span>
          <span dir="auto" className="mt-0.5 line-clamp-2 text-sm text-ink-soft [overflow-wrap:anywhere]">
            {who && <bdi className="text-ink">{who}: </bdi>}
            <Highlight text={messagePreview(h)} query={query} />
          </span>
        </span>
      </Link>
    </li>
  );
}

/** Memoized: the page renders again with every live snapshot (once a second
 * while something downloads) and an account can have thousands of chats. */
const ChatItem = memo(function ChatItem({ chat: c, onChanged, act }: {
  chat: ChatRow;
  onChanged: () => Promise<void>;
  act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const scanState = c.scanning ? "يُفحص الآن…" : c.queued ? "بانتظار الفحص" : null;

  return (
    <li className={`px-4 py-3.5 sm:px-5 ${c.tracked ? "" : "text-ink-soft"}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-3 sm:gap-x-4">
        <Toggle
          checked={c.tracked}
          label={`متابعة ${c.title}`}
          onChange={(v) => act(() => post(`/api/chats/${c.id}/track`, { tracked: v }))}
        />
        <Avatar id={c.id} name={c.title} className={`size-12 text-lg ${c.tracked ? "" : "opacity-70"}`} />
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
                  <Link to={`/library?view=available&chat=${c.id}`} className="text-accent underline underline-offset-4">
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
        <div className="flex flex-wrap gap-2 basis-full sm:basis-auto">
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
              className="inline-flex min-h-11 items-center rounded-lg px-4 font-semibold text-accent transition-colors hover:bg-accent-tint"
            >
              اقرأ الرسائل
            </Link>
          )}
        </div>
      </div>
      {open && c.tracked && <ChatOptions chat={c} onSaved={onChanged} />}
    </li>
  );
});

function ChatOptions({ chat, onSaved }: { chat: ChatRow; onSaved: () => Promise<void> }) {
  // null = this chat follows the general settings.
  const [exts, setExts] = useState<string[] | null>(chat.allowed_exts ? chat.allowed_exts.split(",") : null);
  const [maxMb, setMaxMb] = useState<number | null>(chat.max_size_mb);
  const [auto, setAuto] = useState(chat.auto_download);
  const [saved, setSaved] = useState<string | false>(false);
  // What a choice of its own starts from: the general one.
  const [general, setGeneral] = useState<string[] | null>(null);
  useEffect(() => {
    api<{ allowed_exts: string }>("/api/settings")
      .then((d) => setGeneral(d.allowed_exts.split(",").filter(Boolean)))
      .catch(() => {});
  }, []);

  async function save() {
    const r = await post<{ reopened: number; hidden: number }>(`/api/chats/${chat.id}/settings`, {
      allowed_exts: exts?.length ? exts.join(",") : null,
      max_size_mb: maxMb,
      auto_download: auto,
    });
    setSaved(
      [
        "حُفظت الخيارات.",
        r.reopened ? `عاد إلى القائمة ${count(r.reopened, NOUNS.file)}.` : "",
        r.hidden ? `أُخفي من القائمة ${count(r.hidden, NOUNS.file)} من الأنواع المستثناة.` : "",
      ]
        .filter(Boolean)
        .join(" "),
    );
    await onSaved();
  }

  const own = exts !== null;
  const choose = (v: boolean) => {
    setExts(v ? general ?? FILE_KINDS.flatMap((k) => k.formats.flatMap((f) => f.exts)) : null);
    setSaved(false);
  };

  return (
    <div className="mt-4 space-y-5 rounded-xl bg-canvas p-4 sm:ms-18">
      <label className="flex items-start gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={auto}
          onChange={(e) => {
            setAuto(e.target.checked);
            setSaved(false);
          }}
          className="mt-1 size-5 accent-accent"
        />
        <span>
          <span className="block font-semibold">نزّل الملفات الجديدة تلقائياً</span>
          <span className="block text-sm text-ink-soft">
            كل ملف جديد يظهر في هذه المحادثة يُنزَّل دون أن تختاره. الملفات الموجودة سابقاً لا تتأثر.
          </span>
        </span>
      </label>

      <fieldset>
        <legend className="font-semibold">أنواع الملفات في هذه المحادثة</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {[
            { v: false, label: "كما في الإعدادات العامة" },
            { v: true, label: "أنواع خاصة بهذه المحادثة" },
          ].map((o) => (
            <label
              key={o.label}
              className={`inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-full border px-4 transition-colors ${
                own === o.v ? "border-accent bg-accent-tint font-semibold text-accent-strong" : "border-line bg-white"
              }`}
            >
              <input
                type="radio"
                name={`types-${chat.id}`}
                checked={own === o.v}
                onChange={() => choose(o.v)}
                className="size-4 accent-accent"
              />
              {o.label}
            </label>
          ))}
        </div>
        {own && (
          <div className="mt-3">
            <FormatPicker
              value={exts}
              onChange={(v) => {
                setExts(v);
                setSaved(false);
              }}
            />
          </div>
        )}
      </fieldset>

      <div>
        <label htmlFor={`max-size-${chat.id}`} className="mb-1 block font-semibold">
          أكبر حجم للملف الواحد
        </label>
        <SizeLimit
          id={`max-size-${chat.id}`}
          inherit
          value={maxMb}
          onChange={(mb) => {
            setMaxMb(mb);
            setSaved(false);
          }}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={save}>حفظ الخيارات</Button>
        {saved && <span role="status" className="text-accent">{saved}</span>}
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
