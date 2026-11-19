import { Suspense, lazy, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import { viewerFor } from "../lib/fileTypes";
import { stem } from "../lib/format";
import { Alert, Button, Spinner } from "../components/ui";

const EpubView = lazy(() => import("../components/EpubView"));

interface Doc {
  id: number;
  name: string;
  ext: string | null;
  has_file: boolean;
}

/** Full-height view of a downloaded file: PDF and text in the browser's own
 * viewer, EPUB with epub.js, pictures, video and sound with the browser's
 * players. Other formats can only be downloaded. */
export function ReaderPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<Doc | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Doc>(`/api/documents/${id}`)
      .then(setDoc)
      .catch((e) => setError(e instanceof ApiError ? e.message : "تعذّر فتح الملف."));
  }, [id]);

  const back = () => (window.history.length > 1 ? navigate(-1) : navigate("/library"));

  return (
    <div className="fixed inset-0 z-10 flex flex-col bg-canvas">
      <header className="flex items-center gap-3 border-b border-line bg-white px-4 py-2">
        <Button variant="secondary" onClick={back}>
          رجوع
        </Button>
        <bdi className="flex-1 min-w-0 truncate font-semibold">{doc && stem(doc.name, doc.ext)}</bdi>
        {doc?.has_file && (
          <a
            href={`/api/files/${doc.id}?download=true`}
            className="inline-flex items-center rounded-lg px-4 min-h-11 font-semibold text-accent hover:bg-accent-tint"
          >
            تنزيل إلى جهازي
          </a>
        )}
      </header>
      <main className="flex-1 min-h-0">
        {error ? (
          <div className="p-6">
            <Alert>{error}</Alert>
          </div>
        ) : !doc ? (
          <div className="grid h-full place-items-center text-accent">
            <Spinner className="size-8" />
          </div>
        ) : !doc.has_file ? (
          <div className="p-6">
            <Alert>هذا الملف لم يُنزَّل بعد.</Alert>
          </div>
        ) : doc.ext === ".epub" ? (
          <Suspense
            fallback={
              <div className="grid h-full place-items-center text-accent">
                <Spinner className="size-8" />
              </div>
            }
          >
            <EpubView docId={doc.id} />
          </Suspense>
        ) : doc.ext === ".pdf" || doc.ext === ".txt" ? (
          <iframe title={doc.name} src={`/api/files/${doc.id}`} className="h-full w-full border-0 bg-white" />
        ) : viewerFor(doc.ext) === "image" ? (
          <div className="grid h-full place-items-center overflow-auto bg-ink p-2">
            <img src={`/api/files/${doc.id}`} alt={doc.name} className="max-h-full max-w-full object-contain" />
          </div>
        ) : viewerFor(doc.ext) === "video" ? (
          <div className="grid h-full place-items-center bg-ink">
            <video src={`/api/files/${doc.id}`} controls autoPlay className="max-h-full max-w-full" />
          </div>
        ) : viewerFor(doc.ext) === "audio" ? (
          <div className="grid h-full place-items-center p-6">
            <div className="w-full max-w-xl rounded-2xl border border-line bg-white p-6 text-center">
              <bdi className="block font-semibold [overflow-wrap:anywhere]">{stem(doc.name, doc.ext)}</bdi>
              <audio src={`/api/files/${doc.id}`} controls autoPlay className="mt-4 w-full" />
            </div>
          </div>
        ) : (
          <div className="p-6">
            <Alert kind="info">لا يمكن فتح هذا النوع من الملفات داخل البرنامج. نزّله إلى جهازك وافتحه ببرنامج مناسب.</Alert>
          </div>
        )}
      </main>
    </div>
  );
}
