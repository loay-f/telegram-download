import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api, post } from "../lib/api";
import { NOUNS, bytes, count, date, num, stem } from "../lib/format";
import { STATUS_LABEL, reasonText } from "../lib/labels";
import { useLive } from "../lib/live";
import { Alert, Button, ProgressBar, Spinner } from "../components/ui";

interface Appearance {
  chat_id: number;
  chat_title: string | null;
  msg_id: number;
  posted_at: string | null;
  caption: string | null;
  link: string | null;
}

interface Entry {
  id: number;
  name: string;
  ext: string | null;
  size: number;
  status: string;
  volume: number | null;
  has_file: boolean;
  bytes_downloaded: number;
  last_error: string | null;
  first_seen_at: string | null;
  completed_at: string | null;
  caption: string | null;
  appearances: Appearance[];
  other_names?: string[];
  copies?: number;
}

interface Work {
  key: string;
  series: boolean;
  title: string;
  size: number;
  chats: string[];
  entries: Entry[];
}

interface ChatCount {
  id: number;
  title: string;
  type: string;
  downloaded: number;
  available: number;
}

type View = "downloaded" | "available";

const PAGE = 30;
export const READABLE = new Set([".pdf", ".epub", ".txt"]);

export function LibraryPage() {
  const { revision } = useLive();
  const [sp, setSp] = useSearchParams();
  const view: View = sp.get("view") === "available" ? "available" : "downloaded";
  const chat = sp.get("chat") ?? "";
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [ext, setExt] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [sort, setSort] = useState("recent");
  const [works, setWorks] = useState<Work[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [chats, setChats] = useState<ChatCount[]>([]);
  const [exts, setExts] = useState<{ ext: string; count: number }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(sp);
    if (value) next.set(key, value);
    else next.delete(key);
    setSp(next, { replace: true });
    setSelected(new Set());
  };

  // Search as the user types, after a short pause.
  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    api<ChatCount[]>("/api/library/chats").then(setChats).catch(() => {});
    api<{ exts: { ext: string; count: number }[] }>("/api/library/filters").then((r) => setExts(r.exts)).catch(() => {});
  }, [revision]);

  const params = useCallback(
    (p: number) => {
      const s = new URLSearchParams({ view, page: String(p), page_size: String(PAGE), sort });
      if (query.trim()) s.set("q", query.trim());
      if (ext) s.set("ext", ext);
      if (chat) s.set("chat_id", chat);
      if (from) s.set("date_from", from);
      if (to) s.set("date_to", to);
      return s.toString();
    },
    [view, sort, query, ext, chat, from, to],
  );

  const reload = useCallback(async () => {
    try {
      const r = await api<{ items: Work[]; total: number }>(`/api/library?${params(1)}`);
      setWorks(r.items);
      setTotal(r.total);
      setPage(1);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر تحميل المكتبة.");
    }
  }, [params]);

  useEffect(() => {
    reload();
  }, [reload, revision]);

  async function more() {
    setLoadingMore(true);
    try {
      const r = await api<{ items: Work[] }>(`/api/library?${params(page + 1)}`);
      setWorks((prev) => [...(prev ?? []), ...r.items]);
      setPage(page + 1);
    } finally {
      setLoadingMore(false);
    }
  }

  async function requestDownload(ids: number[], toFront = false) {
    setError(null);
    try {
      const r = await post<{ queued: number }>("/api/documents/download", { ids, to_front: toFront });
      setNotice(r.queued ? `أُضيف إلى قائمة التنزيل: ${count(r.queued, NOUNS.file)}.` : "هذه الملفات في القائمة بالفعل.");
      setSelected(new Set());
      await reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر إضافة الملفات.");
    }
  }

  async function downloadChat() {
    try {
      const r = await post<{ queued: number }>(`/api/chats/${chat}/download-all`);
      setNotice(`أُضيف إلى قائمة التنزيل: ${count(r.queued, NOUNS.file)}.`);
      await reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر إضافة الملفات.");
    }
  }

  async function remove(entry: Entry) {
    try {
      await api(`/api/library/${entry.id}`, { method: "DELETE" });
      setNotice(`حُذف «${entry.name}» من المكتبة والقرص.`);
      await reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر الحذف.");
    }
  }

  const currentChat = chats.find((c) => String(c.id) === chat);
  const chatCount = (c: ChatCount) => (view === "downloaded" ? c.downloaded : c.available);
  const totals = useMemo(
    () => ({
      downloaded: chats.reduce((a, c) => a + c.downloaded, 0),
      available: chats.reduce((a, c) => a + c.available, 0),
    }),
    [chats],
  );
  const pickable = (works ?? []).flatMap((w) => w.entries).filter((e) => e.status === "available" || e.status === "failed");
  const activeFilters = [ext, from, to].filter(Boolean).length;

  return (
    <div className="space-y-6">
      <h1 className="page-title">المكتبة</h1>

      <div role="tablist" className="flex flex-wrap gap-2">
        {(
          [
            ["downloaded", "الكتب المنزّلة"],
            ["available", "متاحة للتنزيل"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={view === key}
            onClick={() => setParam("view", key === "downloaded" ? "" : key)}
            className={`rounded-lg px-4 min-h-11 font-semibold border transition-colors ${
              view === key ? "bg-ink text-white border-ink" : "bg-white border-line hover:border-binding"
            }`}
          >
            {label}
            <span className="ms-2 opacity-80">{num(totals[key])}</span>
          </button>
        ))}
      </div>

      <div className="lg:flex lg:gap-6 lg:items-start">
        {/* Browse by chat */}
        <nav aria-label="المحادثات" className="lg:w-60 lg:shrink-0 mb-4 lg:mb-0 lg:sticky lg:top-6">
          <label className="lg:hidden block">
            <span className="block font-semibold mb-1">المحادثة</span>
            <select
              value={chat}
              onChange={(e) => setParam("chat", e.target.value)}
              className="w-full rounded-lg border border-line bg-white px-3 min-h-11"
            >
              <option value="">كل المحادثات</option>
              {chats.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title} ({num(chatCount(c))})
                </option>
              ))}
            </select>
          </label>
          <ul className="hidden lg:block rounded-xl border border-line bg-white p-2 max-h-[70vh] overflow-y-auto">
            <ChatLink active={!chat} onClick={() => setParam("chat", "")} title="كل المحادثات" n={totals[view]} />
            {chats
              .filter((c) => chatCount(c) > 0 || String(c.id) === chat)
              .map((c) => (
                <ChatLink
                  key={c.id}
                  active={String(c.id) === chat}
                  onClick={() => setParam("chat", String(c.id))}
                  title={c.title}
                  n={chatCount(c)}
                />
              ))}
          </ul>
        </nav>

        <div className="flex-1 min-w-0 space-y-4">
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="ابحث باسم الكتاب أو بكلمة من وصفه"
            aria-label="بحث في المكتبة"
            className="w-full rounded-lg border border-line bg-white px-4 min-h-13 text-lg focus:border-binding focus:outline-none focus:ring-2 focus:ring-binding/25"
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="quiet" aria-expanded={showFilters} onClick={() => setShowFilters(!showFilters)}>
              تصفية النتائج{activeFilters > 0 && ` (${num(activeFilters)})`}
            </Button>
            <label className="flex items-center gap-2 text-ink-soft">
              الترتيب
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value)}
                className="rounded-lg border border-line bg-white px-3 min-h-10 text-ink"
              >
                <option value="recent">{view === "downloaded" ? "الأحدث تنزيلاً" : "الأحدث ظهوراً"}</option>
                <option value="name">حسب الاسم</option>
                <option value="size">الأكبر حجماً</option>
              </select>
            </label>
            <span className="text-ink-soft ms-auto">{works && count(total, NOUNS.book)}</span>
          </div>

          {showFilters && (
            <div className="grid gap-4 sm:grid-cols-3 rounded-xl border border-line bg-white p-4">
              <label className="block">
                <span className="block font-semibold mb-1">نوع الملف</span>
                <select
                  value={ext}
                  onChange={(e) => setExt(e.target.value)}
                  className="w-full rounded-lg border border-line bg-white px-3 min-h-11"
                >
                  <option value="">كل الأنواع</option>
                  {exts.map((e) => (
                    <option key={e.ext} value={e.ext}>
                      {e.ext.toUpperCase()}
                    </option>
                  ))}
                </select>
              </label>
              <DateField label="منشور من تاريخ" value={from} onChange={setFrom} />
              <DateField label="إلى تاريخ" value={to} onChange={setTo} />
            </div>
          )}

          {view === "available" && (
            <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-white px-4 py-3">
              <span className="text-ink-soft">
                {selected.size > 0 ? `محدد: ${count(selected.size, NOUNS.file)}` : "حدد ما تريد تنزيله، أو نزّل كل ملفات محادثة."}
              </span>
              {pickable.length > 0 && (
                <Button
                  variant="quiet"
                  onClick={() =>
                    setSelected(selected.size === pickable.length ? new Set() : new Set(pickable.map((e) => e.id)))
                  }
                >
                  {selected.size === pickable.length ? "إلغاء التحديد" : "تحديد كل ما في الصفحة"}
                </Button>
              )}
              <div className="flex flex-wrap gap-2 ms-auto">
                {selected.size > 0 && <Button onClick={() => requestDownload([...selected])}>نزّل المحدد</Button>}
                {currentChat && currentChat.available > 0 && (
                  <Button variant="secondary" onClick={downloadChat}>
                    نزّل كل ملفات «{currentChat.title}» ({num(currentChat.available)})
                  </Button>
                )}
              </div>
            </div>
          )}

          {notice && <Alert kind="info">{notice}</Alert>}
          {error && <Alert>{error}</Alert>}

          {!works ? (
            <div className="flex items-center gap-3 text-ink-soft">
              <Spinner className="size-5" /> جارٍ التحميل…
            </div>
          ) : works.length === 0 ? (
            <EmptyState view={view} filtered={Boolean(query || activeFilters || chat)} />
          ) : (
            <>
              <ul className="rounded-xl border border-line bg-white divide-y divide-line">
                {works.map((w) => (
                  <WorkRow
                    key={w.key}
                    work={w}
                    view={view}
                    selected={selected}
                    onToggle={(id) => {
                      const next = new Set(selected);
                      if (next.has(id)) next.delete(id);
                      else next.add(id);
                      setSelected(next);
                    }}
                    onDownload={(ids) => requestDownload(ids, true)}
                    onDelete={remove}
                  />
                ))}
              </ul>
              {works.length < total && (
                <div className="text-center">
                  <Button variant="secondary" busy={loadingMore} onClick={more}>
                    عرض المزيد
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function ChatLink({ active, onClick, title, n }: { active: boolean; onClick: () => void; title: string; n: number }) {
  return (
    <li>
      <button
        onClick={onClick}
        aria-current={active ? "true" : undefined}
        className={`w-full flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-start transition-colors ${
          active ? "bg-binding-tint text-binding-dark font-semibold" : "hover:bg-paper"
        }`}
      >
        <bdi className="truncate">{title}</bdi>
        <span className="text-sm text-ink-soft shrink-0">{num(n)}</span>
      </button>
    </li>
  );
}

function EmptyState({ view, filtered }: { view: View; filtered: boolean }) {
  let text: React.ReactNode;
  if (filtered) text = "لا توجد كتب تطابق البحث. جرّب كلمة أخرى أو أزل عوامل التصفية.";
  else if (view === "downloaded")
    text = (
      <>
        لم تُنزّل كتباً بعد. اختر ما تريد من تبويب{" "}
        <Link to="/library?view=available" className="text-binding underline underline-offset-4">
          «متاحة للتنزيل»
        </Link>{" "}
        أو من{" "}
        <Link to="/search" className="text-binding underline underline-offset-4">
          «البحث في تيليغرام»
        </Link>
        .
      </>
    );
  else
    text = (
      <>
        لا توجد ملفات متاحة. فعّل متابعة محادثة من صفحة{" "}
        <Link to="/chats" className="text-binding underline underline-offset-4">
          «المحادثات»
        </Link>{" "}
        ليفحصها البرنامج ويعرض ملفاتها هنا.
      </>
    );
  return <p className="text-ink-soft text-lg">{text}</p>;
}

const VOLUME_WORD = /\s+(ج|جزء|الجزء|مجلد|المجلد|vol|volume|part|pt)\.?$/i;

/** The series name: what all volume names share, cut at a word boundary,
 * without a trailing «ج» / «الجزء». Falls back to the first volume's name. */
function seriesTitle(entries: Entry[]): string {
  const names = entries.map((e) => stem(e.name, e.ext).replace(/[_\-–—]+/g, " ").replace(/\s+/g, " ").trim());
  let prefix = names[0];
  for (const n of names.slice(1)) {
    let i = 0;
    while (i < prefix.length && i < n.length && prefix[i] === n[i]) i++;
    prefix = prefix.slice(0, i);
  }
  if (names.some((n) => n.length > prefix.length && n[prefix.length] !== " ")) {
    prefix = prefix.slice(0, prefix.lastIndexOf(" ") + 1);
  }
  const title = prefix.trim().replace(VOLUME_WORD, "").trim();
  return title.length >= 3 ? title : names[0];
}

function Cover({ entry, view }: { entry: Entry; view: View }) {
  const [failed, setFailed] = useState(false);
  const badge = (entry.ext ?? "؟").replace(".", "").toUpperCase();
  if (view === "downloaded" && !failed) {
    return (
      <img
        src={`/api/thumbs/${entry.id}`}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
        className="w-14 h-[4.6rem] shrink-0 rounded-md border border-line object-cover object-top bg-paper"
      />
    );
  }
  return (
    <span
      className="w-14 h-[4.6rem] shrink-0 rounded-md bg-binding-tint grid place-items-center text-sm font-semibold text-binding-dark"
      dir="ltr"
    >
      {badge}
    </span>
  );
}

function WorkRow({ work, view, selected, onToggle, onDownload, onDelete }: {
  work: Work;
  view: View;
  selected: Set<number>;
  onToggle: (id: number) => void;
  onDownload: (ids: number[]) => void;
  onDelete: (e: Entry) => void;
}) {
  const [open, setOpen] = useState(false);
  const first = work.entries[0];
  if (!work.series) {
    return (
      <li className="px-5 py-4">
        <EntryRow entry={first} view={view} title={stem(first.name, first.ext)} selected={selected} onToggle={onToggle}
          onDownload={onDownload} onDelete={onDelete} showCover />
      </li>
    );
  }
  const pendingIds = work.entries.filter((e) => e.status === "available" || e.status === "failed").map((e) => e.id);
  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start gap-4">
        <Cover entry={first} view={view} />
        <div className="flex-1 min-w-0">
          <bdi className="block text-lg font-semibold break-words">{seriesTitle(work.entries)}</bdi>
          <p className="mt-1 text-sm text-ink-soft">
            {count(work.entries.length, NOUNS.volume)} · {bytes(work.size)} · في: {work.chats.join("، ")}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 basis-full ps-18 sm:basis-auto sm:ps-0">
          {view === "available" && pendingIds.length > 0 && (
            <Button onClick={() => onDownload(pendingIds)}>نزّل كل الأجزاء</Button>
          )}
          <Button variant="quiet" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "إخفاء الأجزاء" : "عرض الأجزاء"}
          </Button>
        </div>
      </div>
      {open && (
        <ul className="mt-4 sm:ms-18 space-y-3 border-s-2 border-binding-tint ps-4">
          {work.entries.map((e) => (
            <li key={e.id}>
              <EntryRow
                entry={e}
                view={view}
                title={e.volume ? `الجزء ${num(e.volume)}` : stem(e.name, e.ext)}
                subtitle={stem(e.name, e.ext)}
                selected={selected}
                onToggle={onToggle}
                onDownload={onDownload}
                onDelete={onDelete}
              />
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function EntryRow({ entry: e, view, title, subtitle, selected, onToggle, onDownload, onDelete, showCover }: {
  entry: Entry;
  view: View;
  title: string;
  subtitle?: string;
  selected: Set<number>;
  onToggle: (id: number) => void;
  onDownload: (ids: number[]) => void;
  onDelete: (e: Entry) => void;
  showCover?: boolean;
}) {
  const { status: live } = useLive();
  const [details, setDetails] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const progress = live?.engine.active.find((a) => a.id === e.id);
  const pickable = e.status === "available" || e.status === "failed";
  const chats = [...new Set(e.appearances.map((a) => a.chat_title ?? ""))];

  return (
    <div>
      <div className="flex flex-wrap items-start gap-4">
        {view === "available" && pickable && (
          <input
            type="checkbox"
            aria-label={`تحديد ${e.name}`}
            checked={selected.has(e.id)}
            onChange={() => onToggle(e.id)}
            className="mt-1.5 size-5 accent-binding"
          />
        )}
        {showCover && <Cover entry={e} view={view} />}
        <div className="flex-1 min-w-0">
          <bdi className={`block break-words ${showCover ? "text-lg font-semibold" : "font-semibold"}`} title={e.name}>
            {title}
          </bdi>
          {subtitle && subtitle !== title && <bdi className="block text-sm text-ink-soft break-words">{subtitle}</bdi>}
          <p className="mt-1 text-sm text-ink-soft">
            {bytes(e.size)}
            {view === "downloaded" ? <> · نُزّل {date(e.completed_at)}</> : <> · {STATUS_LABEL[e.status] ?? e.status}</>}
            {showCover && chats.length > 0 && <> · في: {chats.join("، ")}</>}
          </p>
          {showCover && e.caption && <p className="mt-2 line-clamp-2 text-ink-soft">{e.caption}</p>}
          {e.status === "failed" && <p className="mt-1 text-sm text-danger">{reasonText(e.last_error)}</p>}
          {(e.status === "downloading" || e.status === "pending") && (progress || e.bytes_downloaded > 0) && (
            <div className="mt-2 max-w-md">
              <ProgressBar value={progress?.done ?? e.bytes_downloaded} max={e.size} />
            </div>
          )}
        </div>
        <div className={`flex flex-wrap gap-2 basis-full sm:basis-auto sm:ps-0 ${showCover ? "ps-18" : ""}`}>
          {e.has_file ? (
            <>
              {e.ext && READABLE.has(e.ext) && (
                <Link
                  to={`/read/${e.id}`}
                  className="inline-flex items-center rounded-lg px-4 min-h-11 font-semibold bg-binding text-white hover:bg-binding-dark"
                >
                  قراءة
                </Link>
              )}
              <a
                href={`/api/files/${e.id}?download=true`}
                className="inline-flex items-center rounded-lg px-4 min-h-11 font-semibold border border-line bg-white hover:border-binding hover:text-binding"
              >
                تنزيل إلى جهازي
              </a>
            </>
          ) : pickable ? (
            <Button onClick={() => onDownload([e.id])}>{e.status === "failed" ? "أعد المحاولة" : "نزّل"}</Button>
          ) : null}
          <Button variant="quiet" aria-expanded={details} onClick={() => setDetails(!details)}>
            التفاصيل
          </Button>
        </div>
      </div>
      {details && (
        <div className="mt-4 ms-18 space-y-3 text-sm">
          {(e.copies ?? 1) > 1 && (
            <p className="text-gilt">
              وُجد هذا الكتاب {count(e.copies!, NOUNS.time)} بأسماء أو رفعات مختلفة، وحُفظت نسخة واحدة فقط.
            </p>
          )}
          {e.other_names && e.other_names.length > 0 && (
            <div>
              <div className="font-semibold">أسماء أخرى للملف</div>
              <ul className="mt-1 list-disc ps-5">
                {e.other_names.map((n) => (
                  <li key={n}>
                    <bdi>{n}</bdi>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div>
            <div className="font-semibold">أين نُشر</div>
            <ul className="mt-1 space-y-1">
              {e.appearances.map((a) => (
                <li key={`${a.chat_id}:${a.msg_id}`}>
                  <bdi>{a.chat_title}</bdi> — {date(a.posted_at)}
                  {a.link && (
                    <>
                      {" "}
                      <a href={a.link} target="_blank" rel="noopener noreferrer" className="text-binding underline underline-offset-4">
                        افتح الرسالة في تيليغرام
                      </a>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </div>
          {e.has_file &&
            (confirmDelete ? (
              <div className="space-y-2">
                <Alert>سيُحذف الملف من القرص. يمكنك تنزيله مرة أخرى لاحقاً من قائمة «المتخطاة».</Alert>
                <div className="flex gap-2">
                  <Button variant="danger" onClick={() => onDelete(e)}>
                    نعم، احذفه
                  </Button>
                  <Button variant="secondary" onClick={() => setConfirmDelete(false)}>
                    تراجع
                  </Button>
                </div>
              </div>
            ) : (
              <Button variant="danger" onClick={() => setConfirmDelete(true)}>
                حذف من المكتبة
              </Button>
            ))}
        </div>
      )}
    </div>
  );
}

function DateField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="block">
      <span className="block font-semibold mb-1">{label}</span>
      <input
        type="date"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-line bg-white px-3 min-h-11"
      />
    </label>
  );
}
