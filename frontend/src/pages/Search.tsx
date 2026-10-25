import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, post } from "../lib/api";
import { NOUNS, bytes, count, date, stem } from "../lib/format";
import { STATUS_LABEL } from "../lib/labels";
import { Alert, Button, Field, Spinner } from "../components/ui";

interface Hit {
  chat: { id: number; title: string; type: string };
  msg_id: number;
  date: string | null;
  caption: string | null;
  name: string;
  ext: string | null;
  size: number;
  doc_id: number | null;
  status: string | null;
  has_file: boolean;
  queued?: boolean;
}

const CHAT_TYPE: Record<string, string> = {
  channel: "قناة",
  group: "مجموعة",
  user: "محادثة خاصة",
  bot: "بوت",
  saved: "",
};

export function SearchPage() {
  return (
    <div className="space-y-8">
      <h1 className="page-title">البحث في تيليغرام</h1>
      <GlobalSearch />
      <LinkDownload />
    </div>
  );
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState("");

  async function search(e: FormEvent) {
    e.preventDefault();
    if (q.trim().length < 2) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ items: Hit[] }>(`/api/search?${new URLSearchParams({ q: q.trim() })}`);
      setHits(r.items);
      setSearched(q.trim());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "تعذّر البحث.");
    } finally {
      setBusy(false);
    }
  }

  async function pick(hit: Hit) {
    try {
      const updated = await post<Hit>("/api/search/download", { chat_id: hit.chat.id, msg_id: hit.msg_id });
      setHits((prev) => prev?.map((h) => (h.chat.id === hit.chat.id && h.msg_id === hit.msg_id ? updated : h)) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "تعذّر إضافة الملف.");
    }
  }

  return (
    <section className="space-y-4">
      <p className="text-ink-soft max-w-2xl">
        ابحث باسم كتاب في كل القنوات والمجموعات والمحادثات التي أنت عضو فيها، ثم اختر ما تريد تنزيله. لا حاجة
        لمتابعة المحادثة.
      </p>
      <form onSubmit={search} className="flex flex-wrap gap-3">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="اسم الكتاب أو المؤلف"
          aria-label="ابحث في تيليغرام"
          className="flex-1 min-w-60 rounded-lg border border-line bg-white px-4 min-h-13 text-lg focus:border-binding focus:outline-none focus:ring-2 focus:ring-binding/25"
        />
        <Button type="submit" busy={busy} className="text-lg px-6 min-h-13">
          ابحث
        </Button>
      </form>
      {error && <Alert>{error}</Alert>}
      {busy && !hits && (
        <div className="flex items-center gap-3 text-ink-soft">
          <Spinner className="size-5" /> يبحث في تيليغرام…
        </div>
      )}
      {hits && (
        <>
          <p className="text-ink-soft">
            {hits.length === 0 ? `لا توجد ملفات باسم «${searched}». جرّب كلمة أقصر أو تهجئة أخرى.` : `${count(hits.length, NOUNS.file)} لـ«${searched}»`}
          </p>
          {hits.length > 0 && (
            <ul className="rounded-xl border border-line bg-white divide-y divide-line">
              {hits.map((h) => (
                <li key={`${h.chat.id}:${h.msg_id}`} className="px-5 py-4">
                  <div className="flex flex-wrap items-start gap-4">
                    <span className="mt-1 w-14 shrink-0 rounded-md bg-binding-tint py-1 text-center text-sm font-semibold text-binding-dark" dir="ltr">
                      {(h.ext ?? "؟").replace(".", "").toUpperCase()}
                    </span>
                    <div className="flex-1 min-w-0">
                      <bdi className="block font-semibold break-words" title={h.name}>
                        {stem(h.name, h.ext)}
                      </bdi>
                      <p className="mt-1 text-sm text-ink-soft">
                        {bytes(h.size)} · <bdi>{h.chat.title}</bdi>
                        {CHAT_TYPE[h.chat.type] && <> ({CHAT_TYPE[h.chat.type]})</>} · {date(h.date)}
                      </p>
                      {h.caption && <p className="mt-1 line-clamp-2 text-sm text-ink-soft">{h.caption}</p>}
                    </div>
                    <div className="basis-full ps-18 sm:basis-auto sm:ps-0">
                      <HitAction hit={h} onPick={() => pick(h)} />
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function HitAction({ hit, onPick }: { hit: Hit; onPick: () => void }) {
  const [busy, setBusy] = useState(false);
  if (hit.has_file) {
    return (
      <Link to={`/read/${hit.doc_id}`} className="inline-flex items-center rounded-lg px-4 min-h-11 font-semibold text-binding hover:bg-binding-tint">
        في مكتبتك
      </Link>
    );
  }
  if (hit.status === "pending" || hit.status === "downloading") {
    return <span className="inline-flex min-h-11 items-center text-binding">{STATUS_LABEL[hit.status]}</span>;
  }
  return (
    <Button
      busy={busy}
      onClick={async () => {
        setBusy(true);
        await onPick();
        setBusy(false);
      }}
    >
      نزّل
    </Button>
  );
}

function LinkDownload() {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ kind: "info" | "danger"; text: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      const r = await post<Hit>("/api/links/download", { url });
      setResult({
        kind: "info",
        text: r.has_file
          ? `«${r.name}» موجود في مكتبتك بالفعل.`
          : `أُضيف «${r.name}» إلى قائمة التنزيل، من «${r.chat.title}».`,
      });
      setUrl("");
    } catch (err) {
      setResult({ kind: "danger", text: err instanceof ApiError ? err.message : "تعذّر تنزيل الرابط." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-line bg-white p-5 space-y-4">
      <h2 className="text-lg font-semibold">تنزيل ملف من رابط</h2>
      <form onSubmit={submit} className="space-y-4">
        <Field
          label="رابط الرسالة"
          hint={<>في تيليغرام اضغط على الرسالة التي فيها الملف ثم «نسخ الرابط»، والصقه هنا.</>}
          dir="ltr"
          placeholder="https://t.me/channel/123"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
          className="text-start"
        />
        <Button type="submit" busy={busy}>
          نزّل الملف
        </Button>
      </form>
      {result && <Alert kind={result.kind}>{result.text}</Alert>}
    </section>
  );
}
