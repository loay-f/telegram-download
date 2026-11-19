import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, post } from "../lib/api";
import { NOUNS, bytes, count, num } from "../lib/format";
import { BackIcon, CheckIcon, CloseIcon, FolderIcon } from "../components/icons";
import { Alert, Button, IconButton, Panel, ProgressBar, Spinner } from "../components/ui";

type Mode = "keep" | "copy" | "move";

interface Job {
  state: "running" | "done" | "stopped" | "failed";
  phase: "listing" | "reading";
  folders: string[];
  mode: Mode;
  check_only: boolean;
  files_total: number;
  files_done: number;
  bytes_total: number;
  bytes_done: number;
  current: string | null;
  counts: Record<"new" | "linked" | "already" | "same" | "junk" | "empty" | "filtered" | "failed", number>;
  moved_bytes: number;
  failures: { path: string; error: string }[];
  error: string | null;
}

interface ImportState {
  job: Job | null;
  storage_path: string;
  in_docker: boolean;
}

const MODES: { key: Mode; label: string; detail: string }[] = [
  {
    key: "keep",
    label: "اتركها في مكانها",
    detail:
      "لا يُنسخ ولا يُنقل شيء. يعرف البرنامج أنها عندك فلا ينزّلها مرة أخرى، لكنها لا تُفتح من داخل البرنامج (إلا إن كانت داخل مجلد التخزين).",
  },
  {
    key: "copy",
    label: "انسخها إلى مجلد التخزين",
    detail: "تبقى الملفات الأصلية كما هي، وتُفتح النسخة من داخل البرنامج. تحتاج مساحة إضافية بحجم الملفات.",
  },
  {
    key: "move",
    label: "انقلها إلى مجلد التخزين",
    detail: "تخرج الملفات من مجلدها الحالي إلى مجلد التخزين وتُفتح من داخل البرنامج. لا تحتاج مساحة إضافية إن كانت على القرص نفسه.",
  },
];

export function ImportPage() {
  const [info, setInfo] = useState<ImportState | null>(null);
  const [folders, setFolders] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("keep");
  const [picking, setPicking] = useState(false);
  const [confirmMove, setConfirmMove] = useState(false);
  const [busy, setBusy] = useState<"add" | "check" | "stop" | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** The finished run whose result the user closed. */
  const [dismissed, setDismissed] = useState(false);
  const first = useRef(true);

  const load = useCallback(async () => {
    try {
      const s = await api<ImportState>("/api/import");
      setInfo(s);
      // Opening the page on a run that has a next step (running, stopped
      // half way, or only a check): its folders are the ones to go on with.
      if (first.current && s.job && (s.job.state !== "done" || s.job.check_only)) {
        setFolders(s.job.folders);
        setMode(s.job.mode);
      }
      first.current = false;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر تحميل الصفحة.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const job = info?.job ?? null;
  const running = job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 1000);
    return () => clearInterval(t);
  }, [running, load]);

  async function start(checkOnly: boolean, from: { folders: string[]; mode: Mode } = { folders, mode }) {
    setBusy(checkOnly ? "check" : "add");
    setError(null);
    setConfirmMove(false);
    try {
      setInfo(await post<ImportState>("/api/import", { ...from, check_only: checkOnly }));
      setDismissed(false);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر البدء.");
    } finally {
      setBusy(null);
    }
  }

  async function stop() {
    setBusy("stop");
    try {
      setInfo(await post<ImportState>("/api/import/stop"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر الإيقاف.");
    } finally {
      setBusy(null);
    }
  }

  if (!info) {
    return (
      <div className="space-y-6">
        <h1 className="page-title">إضافة ملفات من جهازك</h1>
        {error ? <Alert>{error}</Alert> : <Spinner className="size-6 text-accent" />}
      </div>
    );
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="page-title">إضافة ملفات من جهازك</h1>
        <p className="mt-2 text-ink-soft">
          عندك ملفات نزّلتها من قبل من تيليغرام أو من مكان آخر؟ أضفها هنا إلى المكتبة حتى تظهر مع بقية ملفاتك ولا
          ينزّلها البرنامج مرة ثانية. الملف الموجود في المكتبة أصلاً لا يُضاف مرتين.
        </p>
      </div>

      {job && running && <Running job={job} onStop={stop} stopping={busy === "stop"} />}

      {job && !running && !dismissed && (
        <Result
          job={job}
          busy={busy === "add"}
          onAddNow={() => start(false, { folders: job.folders, mode: job.mode })}
          onClose={() => setDismissed(true)}
        />
      )}

      {!running && (
        <>
          <Panel title="١. أين الملفات؟">
            {info.in_docker && (
              <div className="mb-4">
                <Alert kind="info">
                  البرنامج يعمل داخل Docker ولا يرى إلا مجلداته. ضع الملفات داخل مجلد التخزين على جهازك، ثم اختر هنا «مجلد
                  التخزين».
                </Alert>
              </div>
            )}
            {folders.length === 0 ? (
              <p className="mb-4 text-ink-soft">
                اختر المجلد الذي فيه الملفات. يُضاف كل ما بداخله، وما في المجلدات التي بداخله أيضاً.
              </p>
            ) : (
              <ul className="mb-4 space-y-2">
                {folders.map((f) => (
                  <li key={f} className="flex items-center gap-3 rounded-lg border border-line bg-canvas px-3 py-1.5">
                    <FolderIcon className="size-6 text-accent" />
                    <bdi dir="ltr" className="min-w-0 flex-1 break-all text-start">
                      {f}
                    </bdi>
                    <IconButton label="إزالة هذا المجلد" onClick={() => setFolders(folders.filter((x) => x !== f))}>
                      <CloseIcon className="size-5" />
                    </IconButton>
                  </li>
                ))}
              </ul>
            )}
            <Button variant={folders.length ? "secondary" : "primary"} onClick={() => setPicking(true)}>
              <FolderIcon className="size-5" />
              {folders.length ? "أضف مجلداً آخر" : "اختر مجلداً"}
            </Button>
          </Panel>

          <Panel title="٢. ماذا نفعل بالملفات؟">
            <div role="radiogroup" aria-label="ماذا نفعل بالملفات؟" className="space-y-3">
              {MODES.map((m) => (
                <label
                  key={m.key}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border px-4 py-3 transition-colors ${
                    mode === m.key ? "border-accent bg-accent-tint" : "border-line hover:bg-canvas"
                  }`}
                >
                  <input
                    type="radio"
                    name="import-mode"
                    checked={mode === m.key}
                    onChange={() => {
                      setMode(m.key);
                      setConfirmMove(false);
                    }}
                    className="mt-1.5 size-5 accent-accent"
                  />
                  <span>
                    <span className="block font-semibold">{m.label}</span>
                    <span className="block text-sm text-ink-soft">{m.detail}</span>
                  </span>
                </label>
              ))}
            </div>
            <p className="mt-3 text-sm text-ink-soft">
              مجلد التخزين: <bdi dir="ltr">{info.storage_path}</bdi>
            </p>
          </Panel>

          <Panel title="٣. ابدأ">
            {confirmMove ? (
              <div className="space-y-3">
                <Alert kind="warn">
                  ستُنقل الملفات من {count(folders.length, NOUNS.folder)} إلى مجلد التخزين ولن تبقى في مكانها الحالي. هل
                  تريد المتابعة؟
                </Alert>
                <div className="flex flex-wrap gap-3">
                  <Button busy={busy === "add"} onClick={() => start(false)}>
                    نعم، انقلها وأضفها
                  </Button>
                  <Button variant="secondary" onClick={() => setConfirmMove(false)}>
                    تراجع
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    className="min-h-12 px-6 text-lg"
                    disabled={folders.length === 0 || busy !== null}
                    busy={busy === "add"}
                    onClick={() => (mode === "move" ? setConfirmMove(true) : start(false))}
                  >
                    أضف إلى المكتبة
                  </Button>
                  <Button
                    variant="quiet"
                    disabled={folders.length === 0 || busy !== null}
                    busy={busy === "check"}
                    onClick={() => start(true)}
                  >
                    افحص أولاً دون تغيير شيء
                  </Button>
                </div>
                <p className="mt-3 text-sm text-ink-soft">
                  {folders.length === 0
                    ? "اختر مجلداً أولاً."
                    : "يقرأ البرنامج كل ملف ليتعرّف عليه، وقد يطول ذلك مع الملفات الكثيرة أو الكبيرة. يمكنك استخدام البرنامج في أثناء ذلك."}
                </p>
              </>
            )}
          </Panel>
        </>
      )}

      {error && <Alert>{error}</Alert>}

      {picking && (
        <FolderPicker
          onClose={() => setPicking(false)}
          onPick={(path) => {
            setFolders((fs) => (fs.includes(path) ? fs : [...fs, path]));
            setPicking(false);
          }}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ progress
function Running({ job, onStop, stopping }: { job: Job; onStop: () => void; stopping: boolean }) {
  const added = job.counts.new + job.counts.linked;
  return (
    <Panel title={job.check_only ? "جارٍ الفحص…" : "جارٍ إضافة الملفات…"}>
      <div aria-live="polite" className="space-y-3">
        {job.phase === "listing" ? (
          <p className="flex items-center gap-3 text-ink-soft">
            <Spinner className="size-5 text-accent" /> جارٍ حصر الملفات في المجلد…
          </p>
        ) : (
          <>
            <ProgressBar value={job.bytes_done} max={job.bytes_total} />
            <p>
              الملف {num(Math.min(job.files_done + 1, job.files_total))} من {num(job.files_total)} —{" "}
              {bytes(job.bytes_done)} من {bytes(job.bytes_total)}
            </p>
            {job.current && (
              <p className="truncate text-sm text-ink-soft">
                <bdi>{job.current}</bdi>
              </p>
            )}
            {added > 0 && (
              <p className="text-sm text-ink-soft">
                {job.check_only ? "جديد حتى الآن" : "أُضيف حتى الآن"}: {count(added, NOUNS.file)}
              </p>
            )}
          </>
        )}
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <Button variant="secondary" busy={stopping} onClick={onStop}>
            إيقاف
          </Button>
          <span className="text-sm text-ink-soft">
            يمكنك الانتقال إلى أي صفحة أخرى؛ العمل مستمر.{!job.check_only && " ما أُضيف حتى الآن يبقى محفوظاً إن أوقفت."}
          </span>
        </div>
      </div>
    </Panel>
  );
}

// -------------------------------------------------------------------- result
function Result({ job, busy, onAddNow, onClose }: {
  job: Job;
  busy: boolean;
  onAddNow: () => void;
  onClose: () => void;
}) {
  const c = job.counts;
  const added = c.new + c.linked;
  const had = c.already + c.same;
  const ignored = c.junk + c.empty;
  const check = job.check_only;

  if (job.state === "failed") {
    return (
      <Alert>
        <strong className="block">لم تتم الإضافة.</strong>
        {job.error}
      </Alert>
    );
  }

  let title: string;
  if (added > 0) title = `${check ? "سيُضاف إلى المكتبة" : "أُضيف إلى المكتبة"}: ${count(added, NOUNS.file)}`;
  else if (job.state === "stopped") title = check ? "أُوقف الفحص قبل أن ينتهي" : "أُوقف قبل أن يُضاف شيء";
  else if (had > 0) title = "لا جديد: كل الملفات هنا موجودة في المكتبة من قبل";
  else if (c.failed > 0) title = "لم يُضف شيء";
  else title = "لم نجد ملفات في هذا المجلد";

  // "label: count", so the sentence reads right for one file and for many.
  const lines: ReactNode[] = [];
  if (c.linked > 0) lines.push(<>عُرفت محادثتها، لأن فحصاً سابقاً وجدها هناك: {count(c.linked, NOUNS.file)}.</>);
  if (c.new > 0)
    lines.push(
      <>بلا محادثة معروفة الآن (تُربط بمحادثتها تلقائياً إذا وجدها فحص لاحق): {count(c.new, NOUNS.file)}.</>,
    );
  if (had > 0 && added > 0)
    lines.push(<>مكررة أو موجودة في المكتبة من قبل، فلم تُضف مرة ثانية: {count(had, NOUNS.file)}.</>);
  if (ignored > 0) lines.push(<>مؤقتة أو مخفية أو فارغة، فتُجوهلت: {count(ignored, NOUNS.file)}.</>);
  if (!check && job.moved_bytes > 0)
    lines.push(
      <>
        {job.mode === "move" ? "نُقل" : "نُسخ"} {bytes(job.moved_bytes)} إلى مجلد التخزين.
      </>,
    );
  if (!check && added > 0 && job.mode === "keep")
    lines.push(<>الملفات بقيت في مكانها ولم يُغيَّر فيها شيء.</>);

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          {added > 0 && !check && <CheckIcon className="size-6 text-accent" />}
          {title}
        </span>
      }
      actions={
        <IconButton label="إغلاق النتيجة" onClick={onClose}>
          <CloseIcon className="size-5" />
        </IconButton>
      }
      className={added > 0 && !check ? "border-accent/40" : ""}
    >
      <div aria-live="polite" className="space-y-4">
        {job.state === "stopped" && (
          <Alert kind="warn">
            أُوقف العمل قبل أن ينتهي{!check && added > 0 && "؛ ما أُضيف محفوظ"}. ابدأ من جديد بالمجلد نفسه ليكمل ما بقي.
          </Alert>
        )}
        {lines.length > 0 && (
          <ul className="list-disc space-y-1 ps-5">
            {lines.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        )}
        {c.failed > 0 && (
          <div>
            <Alert>
              تعذّرت {check ? "قراءة" : "إضافة"} {count(c.failed, NOUNS.file)}.
            </Alert>
            <details className="mt-2">
              <summary className="cursor-pointer font-semibold text-accent">عرض الملفات التي لم تنجح</summary>
              <ul className="thin-scroll mt-2 max-h-64 space-y-2 overflow-y-auto text-sm">
                {job.failures.map((f) => (
                  <li key={f.path}>
                    <bdi dir="ltr" className="block break-all text-start">
                      {f.path}
                    </bdi>
                    <span className="text-danger">{f.error}</span>
                  </li>
                ))}
                {c.failed > job.failures.length && (
                  <li className="text-ink-soft">… و{count(c.failed - job.failures.length, NOUNS.file)} أخرى.</li>
                )}
              </ul>
            </details>
          </div>
        )}
        <div className="flex flex-wrap gap-3">
          {check && added > 0 && (
            <Button busy={busy} onClick={onAddNow}>
              {job.mode === "move" ? "انقلها وأضفها الآن" : "أضفها الآن"}
            </Button>
          )}
          {!check && added > 0 && (
            <Link
              to="/library"
              className="inline-flex min-h-11 items-center rounded-lg bg-accent px-4 font-semibold text-white transition-colors hover:bg-accent-strong"
            >
              افتح المكتبة
            </Link>
          )}
        </div>
        {check && <p className="text-sm text-ink-soft">كان هذا فحصاً فقط: لم يتغيّر شيء في المكتبة ولا في ملفاتك.</p>}
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------- folder picker
interface Shortcut {
  kind: "telegram" | "downloads" | "desktop" | "documents" | "storage" | "home" | "drive" | "root";
  name: string;
  path: string;
}

interface Listing {
  path: string;
  name: string;
  parent: string | null;
  dirs: { name: string; path: string }[];
  files: number;
}

function shortcutLabel(s: Shortcut): string {
  switch (s.kind) {
    case "telegram":
      return "تنزيلات تيليغرام (Telegram Desktop)";
    case "downloads":
      return "التنزيلات";
    case "desktop":
      return "سطح المكتب";
    case "documents":
      return "المستندات";
    case "storage":
      return "مجلد التخزين";
    case "home":
      return "مجلدي الشخصي";
    case "root":
      return "كل مجلدات الجهاز";
    default:
      return `القرص ${s.name}`;
  }
}

/** The server lists its own folders: a browser cannot say where a folder is. */
function FolderPicker({ onPick, onClose }: { onPick: (path: string) => void; onClose: () => void }) {
  const [shortcuts, setShortcuts] = useState<Shortcut[] | null>(null);
  const [here, setHere] = useState<Listing | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    api<{ shortcuts: Shortcut[] }>("/api/import/folders")
      .then((r) => setShortcuts(r.shortcuts))
      .catch((e) => setError(e instanceof ApiError ? e.message : "تعذّر عرض المجلدات."));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function open(path: string) {
    setLoading(true);
    setError(null);
    try {
      setHere(await api<Listing>(`/api/import/folders?path=${encodeURIComponent(path)}`));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر فتح المجلد.");
    } finally {
      setLoading(false);
    }
  }

  const row = "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-start transition-colors hover:bg-canvas";

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/45 p-4" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="اختر المجلد الذي فيه الملفات"
        onMouseDown={(e) => e.stopPropagation()}
        className="flex max-h-[85vh] w-full max-w-xl flex-col rounded-2xl bg-white shadow-xl"
      >
        <header className="flex items-center gap-2 border-b border-line px-3 py-2">
          {here ? (
            <IconButton
              label="رجوع"
              onClick={() => {
                setError(null);
                if (here.parent) open(here.parent);
                else setHere(null);
              }}
            >
              <BackIcon className="size-5" />
            </IconButton>
          ) : (
            <span className="size-11" />
          )}
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-lg font-semibold">{here ? <bdi>{here.name}</bdi> : "اختر المجلد الذي فيه الملفات"}</h2>
            {here && (
              <bdi dir="ltr" className="block truncate text-start text-sm text-ink-soft">
                {here.path}
              </bdi>
            )}
          </div>
          <IconButton label="إغلاق" onClick={onClose}>
            <CloseIcon className="size-5" />
          </IconButton>
        </header>

        <div className="thin-scroll min-h-48 flex-1 overflow-y-auto p-2">
          {error && (
            <div className="p-2">
              <Alert>{error}</Alert>
            </div>
          )}
          {!here ? (
            !shortcuts ? (
              !error && <Spinner className="m-4 size-6 text-accent" />
            ) : (
              <>
                {shortcuts.map((s) => (
                  <button key={s.path} type="button" className={row} onClick={() => open(s.path)}>
                    <FolderIcon className="size-6 text-accent" />
                    <span className="min-w-0 flex-1">
                      <span className="block font-semibold">{shortcutLabel(s)}</span>
                      <bdi dir="ltr" className="block truncate text-start text-sm text-ink-soft">
                        {s.path}
                      </bdi>
                    </span>
                  </button>
                ))}
                <form
                  className="mt-2 border-t border-line p-3"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (typed.trim()) open(typed.trim());
                  }}
                >
                  <label htmlFor="folder-path" className="mb-1 block text-sm font-semibold">
                    أو الصق مسار المجلد هنا
                  </label>
                  <div className="flex gap-2">
                    <input
                      id="folder-path"
                      dir="ltr"
                      value={typed}
                      onChange={(e) => setTyped(e.target.value)}
                      placeholder="D:\Books"
                      className="min-h-11 min-w-0 flex-1 rounded-lg border border-line bg-white px-3"
                    />
                    <Button type="submit" variant="secondary" busy={loading} disabled={!typed.trim()}>
                      افتح
                    </Button>
                  </div>
                </form>
              </>
            )
          ) : here.dirs.length === 0 ? (
            <p className="p-4 text-ink-soft">لا توجد مجلدات أخرى بداخل هذا المجلد.</p>
          ) : (
            here.dirs.map((d) => (
              <button key={d.path} type="button" className={row} disabled={loading} onClick={() => open(d.path)}>
                <FolderIcon className="size-6 text-ink-soft" />
                <bdi className="min-w-0 flex-1 truncate">{d.name}</bdi>
              </button>
            ))
          )}
        </div>

        {here && (
          <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3">
            <span className="text-sm text-ink-soft">
              الملفات فيه مباشرةً: {num(here.files)} · المجلدات بداخله: {num(here.dirs.length)}
            </span>
            <Button onClick={() => onPick(here.path)} busy={loading}>
              اختر هذا المجلد
            </Button>
          </footer>
        )}
      </div>
    </div>
  );
}
