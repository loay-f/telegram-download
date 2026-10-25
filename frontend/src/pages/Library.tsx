import { useEffect, useState } from "react";
import { ApiError, api } from "../lib/api";
import { NOUNS, bytes, count, date, num } from "../lib/format";
import { useLive } from "../lib/live";
import { Alert, Button, Spinner } from "../components/ui";

interface Appearance {
  chat_id: number;
  chat_title: string | null;
  msg_id: number;
  posted_at: string | null;
  caption: string | null;
  link: string | null;
}

interface Book {
  id: number;
  name: string;
  other_names: string[];
  ext: string | null;
  size: number;
  completed_at: string | null;
  copies: number;
  appearances: Appearance[];
}

interface Filters {
  exts: { ext: string; count: number }[];
  chats: { id: number; title: string }[];
}

const PAGE = 30;
const INLINE = new Set([".pdf", ".txt"]);

export function LibraryPage() {
  const { revision } = useLive();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [ext, setExt] = useState("");
  const [chat, setChat] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [sort, setSort] = useState("recent");
  const [items, setItems] = useState<Book[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<Filters | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [showFilters, setShowFilters] = useState(false);

  // Search as the user types, after a short pause.
  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    api<Filters>("/api/library/filters").then(setFilters).catch(() => {});
  }, [revision]);

  function params(p: number) {
    const sp = new URLSearchParams({ page: String(p), page_size: String(PAGE), sort });
    if (query.trim()) sp.set("q", query.trim());
    if (ext) sp.set("ext", ext);
    if (chat) sp.set("chat_id", chat);
    if (from) sp.set("date_from", from);
    if (to) sp.set("date_to", to);
    return sp.toString();
  }

  useEffect(() => {
    let cancelled = false;
    api<{ items: Book[]; total: number }>(`/api/library?${params(1)}`)
      .then((r) => {
        if (cancelled) return;
        setItems(r.items);
        setTotal(r.total);
        setPage(1);
        setError(null);
      })
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : "تعذّر تحميل المكتبة."));
    return () => {
      cancelled = true;
    };
  }, [query, ext, chat, from, to, sort, revision]);

  async function more() {
    setLoadingMore(true);
    try {
      const r = await api<{ items: Book[]; total: number }>(`/api/library?${params(page + 1)}`);
      setItems((prev) => [...(prev ?? []), ...r.items]);
      setPage(page + 1);
    } finally {
      setLoadingMore(false);
    }
  }

  const activeFilters = [ext, chat, from, to].filter(Boolean).length;

  return (
    <div className="space-y-6">
      <h1 className="page-title">المكتبة</h1>

      <div className="space-y-3">
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
              <option value="recent">الأحدث تنزيلاً</option>
              <option value="name">حسب الاسم</option>
              <option value="size">الأكبر حجماً</option>
            </select>
          </label>
          <span className="text-ink-soft ms-auto">{items && count(total, NOUNS.book)}</span>
        </div>

        {showFilters && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 rounded-xl border border-line bg-white p-4">
            <Select label="نوع الملف" value={ext} onChange={setExt}>
              <option value="">كل الأنواع</option>
              {filters?.exts.map((e) => (
                <option key={e.ext} value={e.ext}>
                  {e.ext.toUpperCase()} ({num(e.count)})
                </option>
              ))}
            </Select>
            <Select label="المحادثة" value={chat} onChange={setChat}>
              <option value="">كل المحادثات</option>
              {filters?.chats.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </Select>
            <DateField label="منشور من تاريخ" value={from} onChange={setFrom} />
            <DateField label="إلى تاريخ" value={to} onChange={setTo} />
            {activeFilters > 0 && (
              <div className="sm:col-span-2 lg:col-span-4">
                <Button
                  variant="quiet"
                  onClick={() => {
                    setExt("");
                    setChat("");
                    setFrom("");
                    setTo("");
                  }}
                >
                  إزالة كل عوامل التصفية
                </Button>
              </div>
            )}
          </div>
        )}
      </div>

      {error && <Alert>{error}</Alert>}

      {!items ? (
        <div className="flex items-center gap-3 text-ink-soft">
          <Spinner className="size-5" /> جارٍ التحميل…
        </div>
      ) : items.length === 0 ? (
        <p className="text-ink-soft text-lg">
          {query || activeFilters
            ? "لا توجد كتب تطابق البحث. جرّب كلمة أخرى أو أزل عوامل التصفية."
            : "المكتبة فارغة حتى الآن. فعّل متابعة قناة من صفحة «المحادثات» لتبدأ الكتب بالوصول."}
        </p>
      ) : (
        <>
          <ul className="rounded-xl border border-line bg-white divide-y divide-line">
            {items.map((b) => (
              <BookRow key={b.id} book={b} />
            ))}
          </ul>
          {items.length < total && (
            <div className="text-center">
              <Button variant="secondary" busy={loadingMore} onClick={more}>
                عرض المزيد
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function BookRow({ book: b }: { book: Book }) {
  const [open, setOpen] = useState(false);
  const chats = [...new Set(b.appearances.map((a) => a.chat_title ?? ""))];
  const caption = b.appearances.find((a) => a.caption)?.caption;
  const canOpen = b.ext !== null && INLINE.has(b.ext);

  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start gap-4">
        <span className="mt-1 shrink-0 w-14 rounded-md bg-binding-tint py-1 text-center text-sm font-semibold text-binding-dark" dir="ltr">
          {(b.ext ?? "؟").replace(".", "").toUpperCase()}
        </span>
        <div className="flex-1 min-w-0">
          <bdi className="block text-lg font-semibold break-words" title={b.name}>{stem(b.name, b.ext)}</bdi>
          <p className="mt-1 text-sm text-ink-soft">
            {bytes(b.size)} · نُزّل {date(b.completed_at)} · في: {chats.join("، ")}
          </p>
          {caption && <p className="mt-2 line-clamp-2 text-ink-soft">{caption}</p>}
        </div>
        <div className="flex flex-wrap gap-2 basis-full ps-18 sm:basis-auto sm:ps-0">
          {canOpen && (
            <a
              href={`/api/files/${b.id}`}
              target="_blank"
              rel="noopener"
              className="inline-flex items-center rounded-lg px-4 min-h-11 font-semibold bg-binding text-white hover:bg-binding-dark"
            >
              فتح
            </a>
          )}
          <a
            href={`/api/files/${b.id}?download=true`}
            className="inline-flex items-center rounded-lg px-4 min-h-11 font-semibold border border-line bg-white hover:border-binding hover:text-binding"
          >
            تنزيل
          </a>
          <Button variant="quiet" aria-expanded={open} onClick={() => setOpen(!open)}>
            التفاصيل
          </Button>
        </div>
      </div>
      {open && (
        <div className="mt-4 ms-18 space-y-3 text-sm">
          {b.copies > 1 && (
            <p className="text-gilt">
              وُجد هذا الكتاب {count(b.copies, NOUNS.time)} بأسماء أو رفعات مختلفة، وحُفظت نسخة واحدة فقط.
            </p>
          )}
          {b.other_names.length > 0 && (
            <div>
              <div className="font-semibold">أسماء أخرى للملف</div>
              <ul className="mt-1 list-disc ps-5">
                {b.other_names.map((n) => (
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
              {b.appearances.map((a) => (
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
        </div>
      )}
    </li>
  );
}

/** The type badge shows the format, so names drop the extension (which
 * otherwise renders as "pdf." at the wrong end in right-to-left text). */
function stem(name: string, ext: string | null): string {
  return ext && name.toLowerCase().endsWith(ext) ? name.slice(0, -ext.length) : name;
}

function Select({ label, value, onChange, children }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="block font-semibold mb-1">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-line bg-white px-3 min-h-11"
      >
        {children}
      </select>
    </label>
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
