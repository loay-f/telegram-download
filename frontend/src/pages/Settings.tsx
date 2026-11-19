import { useEffect, useState } from "react";
import { ApiError, api, post } from "../lib/api";
import type { AccountStatus } from "../lib/account";
import { NOUNS, bytes, count, digits } from "../lib/format";
import { useAccount } from "../lib/context";
import { FormatPicker, SizeLimit } from "../components/FormatPicker";
import { Alert, Button, Panel, ProgressBar, Spinner } from "../components/ui";

interface SettingsData {
  allowed_exts: string;
  max_size_mb: number;
  workers: number;
  download_streams: number;
  scan_interval_hours: number;
  max_attempts: number;
  speed_limit_kbps: number;
  schedule_enabled: boolean;
  schedule_start: string;
  schedule_end: string;
  timezone: string;
  summary_enabled: boolean;
  summary_hour: number;
  library_bytes: number;
  storage: { total: number; free: number; partial_bytes: number; path: string };
  /** After saving: files that came back / left the list with the new types. */
  reopened?: number;
  hidden?: number;
}

export function SettingsPage() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [allowed, setAllowed] = useState<string[]>([]);
  const [maxMb, setMaxMb] = useState(0);
  const [workers, setWorkers] = useState(3);
  const [streams, setStreams] = useState(1);
  const [interval, setIntervalH] = useState(6);
  const [attempts, setAttempts] = useState(5);
  const [limitOn, setLimitOn] = useState(false);
  const [limitMb, setLimitMb] = useState("2");
  const [schedOn, setSchedOn] = useState(false);
  const [schedStart, setSchedStart] = useState("01:00");
  const [schedEnd, setSchedEnd] = useState("07:00");
  const [summaryOn, setSummaryOn] = useState(false);
  const [summaryHour, setSummaryHour] = useState(8);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ kind: "info" | "danger"; text: string } | null>(null);

  function apply(d: SettingsData) {
    setData(d);
    setAllowed(d.allowed_exts.split(",").filter(Boolean));
    setMaxMb(d.max_size_mb);
    setWorkers(d.workers);
    setStreams(d.download_streams);
    setIntervalH(d.scan_interval_hours);
    setAttempts(d.max_attempts);
    setLimitOn(d.speed_limit_kbps > 0);
    if (d.speed_limit_kbps > 0) setLimitMb(String(Math.round((d.speed_limit_kbps / 1024) * 10) / 10));
    setSchedOn(d.schedule_enabled);
    setSchedStart(d.schedule_start);
    setSchedEnd(d.schedule_end);
    setSummaryOn(d.summary_enabled);
    setSummaryHour(d.summary_hour);
  }

  useEffect(() => {
    api<SettingsData>("/api/settings").then(apply).catch(() => setMessage({ kind: "danger", text: "تعذّر تحميل الإعدادات." }));
  }, []);

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      const d = await post<SettingsData>("/api/settings", {
        allowed_exts: allowed.join(","),
        max_size_mb: maxMb,
        workers,
        download_streams: streams,
        scan_interval_hours: interval,
        max_attempts: attempts,
        speed_limit_kbps: limitOn ? Math.max(1, Math.round(Number(limitMb) * 1024) || 0) : 0,
        schedule_enabled: schedOn,
        schedule_start: schedStart,
        schedule_end: schedEnd,
        summary_enabled: summaryOn,
        summary_hour: summaryHour,
        // Schedules and the daily summary follow this computer's clock.
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      });
      apply(d);
      const notes = [
        d.reopened ? `عاد إلى القائمة ${count(d.reopened, NOUNS.file)} كانت متخطاة.` : "",
        d.hidden ? `أُخفي من القائمة ${count(d.hidden, NOUNS.file)} من الأنواع المستثناة (لم يُحذف شيء مما نُزّل).` : "",
      ];
      setMessage({ kind: "info", text: ["حُفظت الإعدادات.", ...notes].filter(Boolean).join(" ") });
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
        {message ? <Alert>{message.text}</Alert> : <Spinner className="size-6 text-accent" />}
      </div>
    );
  }

  const used = data.storage.total - data.storage.free;

  return (
    <div className="space-y-6 max-w-3xl">
      <h1 className="page-title">الإعدادات</h1>

      <Panel title="أنواع الملفات">
        <p className="mb-4 text-ink-soft">
          شغّل الأنواع التي تريد أن يجدها البرنامج في المحادثات التي تتابعها. ما تطفئه يُتجاهل، ويمكنك داخل كل
          نوع استثناء صيغ بعينها.
        </p>
        <FormatPicker value={allowed} onChange={setAllowed} />
        <div className="mt-5">
          <label htmlFor="max-size" className="block font-semibold">
            أكبر حجم للملف الواحد
          </label>
          <p className="mb-2 text-sm text-ink-soft">
            الملفات الأكبر من ذلك تُتجاهل. مفيد مع الفيديو حتى لا يمتلئ القرص.
          </p>
          <SizeLimit id="max-size" value={maxMb} onChange={(mb) => setMaxMb(mb ?? 0)} />
        </div>
      </Panel>

      <Panel title="أوقات التنزيل وسرعته">
        <div className="space-y-5">
          <CheckRow
            checked={schedOn}
            onChange={setSchedOn}
            label="نزّل في ساعات محددة فقط"
            hint="مفيد إن كان الإنترنت أرخص أو أسرع ليلاً. خارج هذه الساعات تنتظر الملفات دورها."
          />
          {schedOn && (
            <div className="flex flex-wrap items-end gap-4 ps-8">
              <TimeField label="من الساعة" value={schedStart} onChange={setSchedStart} />
              <TimeField label="إلى الساعة" value={schedEnd} onChange={setSchedEnd} />
            </div>
          )}
          <CheckRow
            checked={limitOn}
            onChange={setLimitOn}
            label="حدّد سرعة التنزيل"
            hint="حتى يبقى الإنترنت سريعاً لبقية أفراد البيت."
          />
          {limitOn && (
            <label className="block ps-8">
              <span className="block font-semibold mb-1">أقصى سرعة (ميغابايت في الثانية)</span>
              <input
                type="number"
                min={0.1}
                step={0.1}
                value={limitMb}
                onChange={(e) => setLimitMb(e.target.value)}
                className="w-32 rounded-lg border border-line bg-white px-3 min-h-11"
              />
            </label>
          )}
        </div>
      </Panel>

      <Panel title="ملخص يومي">
        <div className="space-y-4">
          <CheckRow
            checked={summaryOn}
            onChange={setSummaryOn}
            label="أرسل لي ملخصاً يومياً في «الرسائل المحفوظة» في تيليغرام"
            hint="ما نُزّل، وما وُجد من ملفات جديدة، وما لم ينجح تنزيله. لا يُرسل شيء في الأيام الهادئة."
          />
          {summaryOn && (
            <label className="block ps-8">
              <span className="block font-semibold mb-1">وقت الإرسال</span>
              <select
                value={summaryHour}
                onChange={(e) => setSummaryHour(Number(e.target.value))}
                className="rounded-lg border border-line bg-white px-3 min-h-11"
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {digits(`${String(h).padStart(2, "0")}:00`)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
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
            label="أجزاء الملف الواحد التي تُطلب معاً (تجريبي)"
            hint="١ هو المعتاد: جزء بعد جزء. رقم أكبر قد يسرّع الملفات الكبيرة، وقد يجعل تيليغرام يطلب الانتظار فتتوقف كل التنزيلات مؤقتاً، خاصة في الحسابات غير المميزة. جرّب ٢ أو ٤ وراقب السرعة، وأعده إلى ١ إن ظهر الانتظار."
            value={streams}
            min={1}
            max={8}
            onChange={setStreams}
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
          الملفات المحفوظة: {bytes(data.library_bytes)}
          {data.storage.partial_bytes > 0 && <> · تنزيلات غير مكتملة: {bytes(data.storage.partial_bytes)}</>}
        </p>
        <p className="mt-1 text-ink-soft">
          الملفات محفوظة في مجلد لكل محادثة داخل المجلد <bdi dir="ltr">library</bdi> في مجلد التخزين.
        </p>
        <details className="mt-4">
          <summary className="cursor-pointer text-accent font-semibold">نقل المكتبة إلى قرص آخر</summary>
          <div className="mt-3 space-y-2 text-ink-soft">
            <p>
              مكان التخزين يُحدَّد عند تشغيل البرنامج ولا يمكن تغييره من هنا، لأن البرنامج يعمل داخل Docker ولا يرى
              إلا المجلد الذي حُدِّد له. لنقل المكتبة:
            </p>
            <ol className="list-decimal ps-5 space-y-1">
              <li>
                أوقف البرنامج: <bdi dir="ltr" className="font-mono text-ink">docker compose stop</bdi>
              </li>
              <li>انسخ مجلد التخزين الحالي كاملاً إلى القرص الجديد، مثلاً <bdi dir="ltr">D:/TGLibrary</bdi>.</li>
              <li>
                في ملف <bdi dir="ltr">.env</bdi> غيّر السطر إلى{" "}
                <bdi dir="ltr" className="font-mono text-ink">STORAGE_HOST_PATH=D:/TGLibrary</bdi>
              </li>
              <li>
                شغّل البرنامج: <bdi dir="ltr" className="font-mono text-ink">docker compose up -d</bdi>
              </li>
              <li>بعد التأكد أن الملفات تظهر وتُفتح، احذف المجلد القديم.</li>
            </ol>
          </div>
        </details>
      </Panel>

      <AccountPanel />
    </div>
  );
}

function CheckRow({ checked, onChange, label, hint }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <label className="flex items-start gap-3 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1 size-5 accent-accent"
      />
      <span>
        <span className="block font-semibold">{label}</span>
        {hint && <span className="block text-sm text-ink-soft">{hint}</span>}
      </span>
    </label>
  );
}

function TimeField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="block">
      <span className="block font-semibold mb-1">{label}</span>
      <input
        type="time"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-line bg-white px-3 min-h-11"
      />
    </label>
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
            سيتوقف التنزيل ويُحذف ملف الجلسة. الملفات التي نُزّلت تبقى محفوظة. ستحتاج إلى رمز جديد من تيليغرام
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
