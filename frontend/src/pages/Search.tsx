import { useState, type FormEvent } from "react";
import { ApiError, api, post } from "../lib/api";
import { NOUNS, count } from "../lib/format";
import { FileHitRow, type Hit, downloadHit, sameHit } from "../components/FileHit";
import { Alert, Button, Field, Spinner } from "../components/ui";

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
      const updated = await downloadHit(hit);
      setHits((prev) => prev?.map((h) => (sameHit(h, hit) ? updated : h)) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "تعذّر إضافة الملف.");
    }
  }

  return (
    <section className="space-y-4">
      <p className="text-ink-soft max-w-2xl">
        ابحث باسم كتاب أو ملف في كل القنوات والمجموعات والمحادثات التي أنت عضو فيها، ثم اختر ما تريد تنزيله. لا حاجة
        لمتابعة المحادثة.
      </p>
      <form onSubmit={search} className="flex flex-wrap gap-3">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="اسم الكتاب أو الملف"
          aria-label="ابحث في تيليغرام"
          className="flex-1 min-w-60 rounded-lg border border-line bg-white px-4 min-h-13 text-lg focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25"
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
                <FileHitRow key={`${h.chat.id}:${h.msg_id}`} hit={h} onPick={() => pick(h)} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
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
