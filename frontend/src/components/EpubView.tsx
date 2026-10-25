import { useEffect, useRef, useState } from "react";
import ePub, { type Book, type Rendition } from "epubjs";
import { Alert, Button, Spinner } from "./ui";

/** Paginated EPUB reader. Arabic books read right to left, so "next" sits on
 * the left and the left arrow key turns forward. */
export default function EpubView({ docId }: { docId: number }) {
  const host = useRef<HTMLDivElement>(null);
  const rendition = useRef<Rendition | null>(null);
  const [rtl, setRtl] = useState(true);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let book: Book | null = null;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/files/${docId}?download=true`, { credentials: "same-origin" });
        if (!res.ok) throw new Error(String(res.status));
        const data = await res.arrayBuffer();
        if (cancelled || !host.current) return;
        book = ePub(data);
        const r = book.renderTo(host.current, { width: "100%", height: "100%", spread: "none", allowScriptedContent: false });
        rendition.current = r;
        await book.ready;
        const meta = book.packaging?.metadata as { direction?: string; language?: string } | undefined;
        const dir = meta?.direction || (meta?.language?.startsWith("ar") ? "rtl" : "") || "rtl";
        setRtl(dir === "rtl");
        await r.display();
        if (!cancelled) setState("ready");
      } catch {
        if (!cancelled) setState("error");
      }
    })();
    return () => {
      cancelled = true;
      book?.destroy();
    };
  }, [docId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const r = rendition.current;
      if (!r) return;
      if (e.key === "ArrowLeft") (rtl ? r.next() : r.prev());
      if (e.key === "ArrowRight") (rtl ? r.prev() : r.next());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rtl]);

  if (state === "error") {
    return (
      <div className="p-6">
        <Alert>تعذّر فتح هذا الملف. قد يكون تالفاً أو بصيغة غير مدعومة؛ جرّب تنزيله وفتحه ببرنامج آخر.</Alert>
      </div>
    );
  }

  const next = () => rendition.current?.next();
  const prev = () => rendition.current?.prev();
  return (
    <div className="flex h-full flex-col">
      <div className="relative flex-1 min-h-0 bg-white">
        <div ref={host} className="absolute inset-0 mx-auto max-w-3xl px-4" />
        {state === "loading" && (
          <div className="absolute inset-0 grid place-items-center text-binding">
            <Spinner className="size-8" />
          </div>
        )}
      </div>
      <nav className="flex justify-between gap-3 border-t border-line bg-white px-4 py-2" dir={rtl ? "rtl" : "ltr"}>
        <Button variant="secondary" onClick={prev}>
          الصفحة السابقة
        </Button>
        <Button variant="secondary" onClick={next}>
          الصفحة التالية
        </Button>
      </nav>
    </div>
  );
}
