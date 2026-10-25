import { useEffect, useState } from "react";
import { ApiError, api, post } from "../lib/api";
import type { AccountStatus } from "../lib/account";
import { NOUNS, bytes, count } from "../lib/format";
import { useAccount } from "../lib/context";
import { Alert, Button, Panel, ProgressBar, Spinner } from "../components/ui";

interface SettingsData {
  allowed_exts: string;
  max_size_mb: number;
  workers: number;
  scan_interval_hours: number;
  max_attempts: number;
  library_bytes: number;
  storage: { total: number; free: number; partial_bytes: number; path: string };
  reopened?: number;
}

const COMMON_TYPES: { ext: string; label: string }[] = [
  { ext: "pdf", label: "PDF" },
  { ext: "epub", label: "EPUB" },
  { ext: "djvu", label: "DjVu" },
  { ext: "mobi", label: "MOBI" },
  { ext: "azw3", label: "AZW3 (كندل)" },
  { ext: "doc", label: "Word قديم (DOC)" },
  { ext: "docx", label: "Word (DOCX)" },
  { ext: "txt", label: "نص (TXT)" },
  { ext: "md", label: "Markdown" },
  { ext: "rtf", label: "RTF" },
  { ext: "odt", label: "ODT" },
  { ext: "zip", label: "ZIP مضغوط" },
  { ext: "rar", label: "RAR مضغوط" },
  { ext: "7z", label: "7z مضغوط" },
];

export function SettingsPage() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [exts, setExts] = useState<Set<string>>(new Set());
  const [extra, setExtra] = useState("");
  const [maxMb, setMaxMb] = useState("0");
  const [workers, setWorkers] = useState(3);
  const [interval, setIntervalH] = useState(6);
  const [attempts, setAttempts] = useState(5);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ kind: "info" | "danger"; text: string } | null>(null);

  function apply(d: SettingsData) {
    setData(d);
    const all = d.allowed_exts.split(",").filter(Boolean);
    const common = new Set(COMMON_TYPES.map((t) => t.ext));
    setExts(new Set(all.filter((e) => common.has(e))));
    setExtra(all.filter((e) => !common.has(e)).join(", "));
    setMaxMb(String(d.max_size_mb));
    setWorkers(d.workers);
    setIntervalH(d.scan_interval_hours);
    setAttempts(d.max_attempts);
  }

  useEffect(() => {
    api<SettingsData>("/api/settings").then(apply).catch(() => setMessage({ kind: "danger", text: "تعذّر تحميل الإعدادات." }));
  }, []);

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      const allowed = [...exts, ...extra.split(/[,،\s]+/).map((e) => e.trim().replace(/^\./, "")).filter(Boolean)];
      const d = await post<SettingsData>("/api/settings", {
        allowed_exts: allowed.join(","),
        max_size_mb: Math.max(0, Number(maxMb) || 0),
        workers,
        scan_interval_hours: interval,
        max_attempts: attempts,
      });
      apply(d);
      setMessage({
        kind: "info",
        text: d.reopened
          ? `حُفظت الإعدادات. أُضيفت إلى قائمة التنزيل ${count(d.reopened, NOUNS.file)} كانت متخطاة.`
          : "حُفظت الإعدادات.",
      });
    } catch (e) {
      setMessage({ kind: "danger", text: e instanceof ApiError ? e.message : "تعذّر الحفظ." });
    } finally {
      setSaving(false);
    }
  }

  if (!data) {
    return (
      <div className="space-y-6">
        <h1 className="page-title">الإعدادات</h1>
        {message ? <Alert>{message.text}</Alert> : <Spinner className="size-6 text-binding" />}
      </div>
    );
  }

  const used = data.storage.total - data.storage.free;

  return (
    <div className="space-y-6 max-w-3xl">
      <h1 className="page-title">الإعدادات</h1>

      <Panel title="أنواع الملفات التي تُنزَّل">
        <div className="grid gap-2 sm:grid-cols-2">
          {COMMON_TYPES.map((t) => (
            <label key={t.ext} className="flex items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-paper cursor-pointer">
              <input
                type="checkbox"
                checked={exts.has(t.ext)}
                onChange={(e) => {
                  const next = new Set(exts);
                  if (e.target.checked) next.add(t.ext);
                  else next.delete(t.ext);
                  setExts(next);
                }}
                className="size-5 accent-binding"
              />
              {t.label}
            </label>
          ))}
        </div>
        <label className="block mt-4">
          <span className="block font-semibold mb-1">أنواع أخرى</span>
          <input
            dir="ltr"
            value={extra}
            onChange={(e) => setExtra(e.target.value)}
            placeholder="cbz, chm"
            className="w-full rounded-lg border border-line bg-white px-3 min-h-11 text-start"
          />
          <span className="block text-sm text-ink-soft mt-1">افصل بينها بفواصل.</span>
        </label>
        <label className="block mt-4">
          <span className="block font-semibold mb-1">أقصى حجم للملف (ميغابايت)</span>
          <input
            type="number"
            min={0}
            value={maxMb}
            onChange={(e) => setMaxMb(e.target.value)}
            className="w-40 rounded-lg border border-line bg-white px-3 min-h-11"
          />
          <span className="block text-sm text-ink-soft mt-1">صفر يعني بلا حد.</span>
        </label>
      </Panel>

      <details className="group rounded-xl border border-line bg-white">
        <summary className="cursor-pointer list-none px-5 py-4 text-lg font-semibold flex items-center justify-between">
          إعدادات متقدمة
          <svg aria-hidden viewBox="0 0 20 20" className="size-5 text-ink-soft transition-transform group-open:rotate-180">
            <path d="M5 7.5l5 5 5-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </summary>
        <div className="px-5 pb-5 space-y-5">
          <NumberField
            label="عدد التنزيلات في الوقت نفسه"
            hint="رقم أكبر أسرع، لكنه قد يجعل تيليغرام يطلب الانتظار أكثر. المقترح: ٣."
            value={workers}
            min={1}
            max={10}
            onChange={setWorkers}
          />
          <NumberField
            label="إعادة فحص المحادثات كل (ساعة)"
            value={interval}
            min={1}
            max={168}
            onChange={setIntervalH}
          />
          <NumberField
            label="عدد المحاولات قبل اعتبار الملف فاشلاً"
            value={attempts}
            min={1}
            max={50}
            onChange={setAttempts}
          />
        </div>
      </details>

      <div className="flex items-center gap-4">
        <Button onClick={save} busy={saving} className="text-lg px-6">
          حفظ الإعدادات
        </Button>
      </div>
      {message && <Alert kind={message.kind}>{message.text}</Alert>}

      <Panel title="مساحة التخزين">
        <ProgressBar value={used} max={data.storage.total} />
        <p className="mt-3">
          مستخدم {bytes(used)} من {bytes(data.storage.total)} — المتبقي <strong>{bytes(data.storage.free)}</strong>
        </p>
        <p className="mt-1 text-ink-soft">
          الكتب المحفوظة: {bytes(data.library_bytes)}
          {data.storage.partial_bytes > 0 && <> · تنزيلات غير مكتملة: {bytes(data.storage.partial_bytes)}</>}
        </p>
      </Panel>

      <AccountPanel />
    </div>
  );
}

function NumberField({ label, hint, value, min, max, onChange }: {
  label: string;
  hint?: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="block font-semibold mb-1">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Math.min(max, Math.max(min, Number(e.target.value) || min)))}
        className="w-32 rounded-lg border border-line bg-white px-3 min-h-11"
      />
      {hint && <span className="block text-sm text-ink-soft mt-1">{hint}</span>}
    </label>
  );
}

function AccountPanel() {
  const { account, setAccount } = useAccount();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function logout() {
    setBusy(true);
    setError(null);
    try {
      setAccount(await post<AccountStatus>("/api/telegram/logout"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "تعذّر تسجيل الخروج.");
      setBusy(false);
    }
  }

  return (
    <Panel title="الحساب">
      <p>
        متصل بتيليغرام باسم <strong>{account.me?.name}</strong>
        {account.me?.phone && (
          <>
            {" "}— <bdi dir="ltr">+{account.me.phone}</bdi>
          </>
        )}
      </p>
      <div className="mt-4 flex flex-wrap gap-3">
        <Button
          variant="secondary"
          onClick={async () => {
            await post("/api/auth/logout");
            window.location.reload();
          }}
        >
          الخروج من البرنامج
        </Button>
        {!confirming && (
          <Button variant="danger" onClick={() => setConfirming(true)}>
            تسجيل الخروج من تيليغرام
          </Button>
        )}
      </div>
      {confirming && (
        <div className="mt-4 space-y-3">
          <Alert>
            سيتوقف التنزيل ويُحذف ملف الجلسة. الكتب التي نُزّلت تبقى محفوظة. ستحتاج إلى رمز جديد من تيليغرام
            للدخول مرة أخرى.
          </Alert>
          <div className="flex gap-3">
            <Button variant="danger" busy={busy} onClick={logout}>
              نعم، سجّل الخروج من تيليغرام
            </Button>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              تراجع
            </Button>
          </div>
        </div>
      )}
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
    </Panel>
  );
}
