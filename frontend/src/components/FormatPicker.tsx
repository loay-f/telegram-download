import { type ComponentType, type SVGProps, useEffect, useRef, useState } from "react";
import { FILE_KINDS, type FileKind, SIZE_LIMITS, otherExts, parseExts } from "../lib/fileTypes";
import { num } from "../lib/format";
import { ArchiveIcon, BooksIcon, CheckIcon, ChevronIcon, CloseIcon, ImageIcon, MusicIcon, VideoIcon } from "./icons";
import { Toggle } from "./ui";

const KIND_ICON: Record<FileKind["key"], ComponentType<SVGProps<SVGSVGElement>>> = {
  books: BooksIcon,
  images: ImageIcon,
  video: VideoIcon,
  audio: MusicIcon,
  archives: ArchiveIcon,
};

/** Chooses which files are wanted, by kind: a switch per kind, and inside
 * each kind the formats to leave out. `value` is the list of file extensions
 * (no dots) the rest of the app works with. */
export function FormatPicker({ value, onChange }: { value: string[]; onChange: (exts: string[]) => void }) {
  const chosen = new Set(value);
  const [open, setOpen] = useState<string | null>(null);
  // What a kind had before it was switched off, so switching it back on
  // doesn't forget the formats that were left out.
  const before = useRef<Record<string, string[]>>({});
  // Kept as typed, so a half-written list isn't rewritten under the cursor.
  const [others, setOthers] = useState(() => otherExts(value).join(", "));
  const otherList = otherExts(value).join(",");
  useEffect(() => {
    setOthers((now) => (parseExts(now).join(",") === otherList ? now : otherList.replace(/,/g, ", ")));
  }, [otherList]);

  function set(exts: string[], on: boolean) {
    const next = new Set(chosen);
    for (const e of exts) {
      if (on) next.add(e);
      else next.delete(e);
    }
    onChange([...next]);
  }

  return (
    <div className="space-y-3">
      {FILE_KINDS.map((kind) => {
        const Icon = KIND_ICON[kind.key];
        const all = kind.formats.flatMap((f) => f.exts);
        const picked = kind.formats.filter((f) => f.exts.some((e) => chosen.has(e)));
        const on = picked.length > 0;
        const some = on && picked.length < kind.formats.length;
        const left = kind.formats.filter((f) => !picked.includes(f));
        const toggle = (v: boolean) => {
          if (!v) before.current[kind.key] = all.filter((e) => chosen.has(e));
          const back = before.current[kind.key];
          set(v && back?.length ? back : all, v);
        };
        const expanded = open === kind.key;
        return (
          <div key={kind.key} className={`rounded-xl border transition-colors ${on ? "border-accent/40 bg-white" : "border-line bg-canvas/60"}`}>
            <div className="flex items-center gap-3 px-3 py-3 sm:px-4">
              <span
                aria-hidden
                className={`grid size-11 shrink-0 place-items-center rounded-full transition-colors ${
                  on ? "bg-accent text-white" : "bg-line/70 text-ink-soft"
                }`}
              >
                <Icon />
              </span>
              <div className="min-w-0 flex-1">
                <div className={`font-semibold ${on ? "" : "text-ink-soft"}`}>{kind.title}</div>
                <div className="text-sm text-ink-soft">
                  {!some ? (
                    kind.hint
                  ) : left.length <= picked.length ? (
                    <>
                      مستثنى: <span className="font-semibold text-ink">{left.map((f) => f.label).join("، ")}</span>
                    </>
                  ) : (
                    <>
                      فقط: <span className="font-semibold text-ink">{picked.map((f) => f.label).join("، ")}</span>
                    </>
                  )}
                </div>
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : kind.key)}
                  className="mt-1 inline-flex items-center gap-1 rounded text-sm font-semibold text-accent hover:underline"
                >
                  {expanded
                    ? "إخفاء الأنواع"
                    : some
                      ? `غيّر الأنواع المستثناة (${num(left.length)})`
                      : "استثنِ بعض الأنواع"}
                  <ChevronIcon className={`size-4 transition-transform ${expanded ? "rotate-180" : ""}`} />
                </button>
              </div>
              <Toggle checked={on} label={kind.title} onChange={toggle} />
            </div>
            {expanded && (
              <div className="border-t border-line px-3 py-3 sm:px-4">
                <p className="mb-2.5 text-sm text-ink-soft">
                  اضغط على النوع الذي لا تريده ليُستثنى، واضغط عليه مرة أخرى ليعود.
                </p>
                <div className="flex flex-wrap gap-2">
                  {kind.formats.map((f) => {
                    const active = f.exts.some((e) => chosen.has(e));
                    return (
                      <button
                        key={f.label}
                        type="button"
                        aria-pressed={active}
                        aria-label={`${f.label}: ${active ? "يُنزَّل" : "مستثنى"}`}
                        title={active ? "اضغط للاستثناء" : "مستثنى — اضغط لإعادته"}
                        onClick={() => set(f.exts, !active)}
                        className={`inline-flex min-h-10 items-center gap-1.5 rounded-full border px-3.5 transition-colors ${
                          active
                            ? "border-accent bg-accent-tint font-semibold text-accent-strong"
                            : "border-dashed border-ink-soft/50 bg-white text-ink-soft hover:border-accent"
                        }`}
                      >
                        {active ? <CheckIcon className="size-4" /> : <CloseIcon className="size-4" />}
                        <span className={active ? "" : "line-through"}>{f.label}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        );
      })}

      {value.length === 0 && (
        <p role="alert" className="rounded-lg bg-warn-tint px-4 py-2.5 text-[#6f4f12]">
          لم تختر أي نوع، فلن يلتقط البرنامج أي ملف. شغّل نوعاً واحداً على الأقل.
        </p>
      )}

      <details className="group rounded-xl border border-line bg-white">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-ink-soft">
          <span>
            أنواع أخرى غير مذكورة
            {otherList && <span className="ms-2 text-sm">({otherList.split(",").join("، ")})</span>}
          </span>
          <ChevronIcon className="size-5 transition-transform group-open:rotate-180" />
        </summary>
        <label className="block px-4 pb-4">
          <span className="block text-sm text-ink-soft">
            إن كنت تريد نوعاً ليس في القائمة، اكتب الأحرف التي ينتهي بها اسم الملف بعد النقطة، مثل
            <bdi dir="ltr"> cbz </bdi>
            وافصل بين الأنواع بفاصلة.
          </span>
          <input
            dir="ltr"
            value={others}
            onChange={(e) => {
              setOthers(e.target.value);
              onChange([...value.filter((x) => !otherExts([x]).length), ...otherExts(parseExts(e.target.value))]);
            }}
            placeholder="cbz, chm"
            className="mt-2 min-h-11 w-full rounded-lg border border-line bg-white px-3 text-start focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25"
          />
        </label>
      </details>
    </div>
  );
}

/** "Don't take files larger than": a few sizes in plain words. `inherit`
 * adds the choice of following the general setting (null). */
export function SizeLimit({ value, onChange, inherit, id }: {
  value: number | null;
  onChange: (mb: number | null) => void;
  inherit?: boolean;
  id?: string;
}) {
  const listed = value === null || SIZE_LIMITS.some((s) => s.mb === value);
  return (
    <select
      id={id}
      value={value === null ? "inherit" : String(value)}
      onChange={(e) => onChange(e.target.value === "inherit" ? null : Number(e.target.value))}
      className="min-h-11 rounded-lg border border-line bg-white px-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25"
    >
      {inherit && <option value="inherit">كما في الإعدادات العامة</option>}
      {SIZE_LIMITS.map((s) => (
        <option key={s.mb} value={s.mb}>
          {s.label}
        </option>
      ))}
      {!listed && <option value={value!}>{num(value!)} ميغابايت</option>}
    </select>
  );
}
