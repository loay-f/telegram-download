const nf = new Intl.NumberFormat("ar-EG");
const nf1 = new Intl.NumberFormat("ar-EG", { maximumFractionDigits: 1 });

export const num = (n: number) => nf.format(n);

/** A file name without its extension. Shown next to a type badge; otherwise
 * ".pdf" renders at the wrong end of right-to-left text. */
export function stem(name: string, ext: string | null): string {
  return ext && name.toLowerCase().endsWith(ext) ? name.slice(0, -ext.length) : name;
}

/** "01:30" -> "٠١:٣٠" */
export const digits = (s: string) => s.replace(/[0-9]/g, (d) => "٠١٢٣٤٥٦٧٨٩"[Number(d)]);

const UNITS = ["بايت", "ك.ب", "م.ب", "ج.ب", "ت.ب"];

export function bytes(n: number | null | undefined): string {
  if (!n || n <= 0) return `${nf.format(0)} ${UNITS[0]}`;
  let i = 0;
  let v = n;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i++;
  }
  return `${i === 0 ? nf.format(v) : nf1.format(v)} ${UNITS[i]}`;
}

export function speed(bps: number): string {
  return `${bytes(bps)}/ث`;
}

/** "٣ دقائق", "ساعة", "٤٥ ثانية" — plain Arabic durations. */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return plural(s, "ثانية", "ثانيتان", "ثوانٍ", "ثانية");
  const m = Math.round(s / 60);
  if (m < 60) return plural(m, "دقيقة", "دقيقتان", "دقائق", "دقيقة");
  const h = Math.round(m / 60);
  if (h < 24) return plural(h, "ساعة", "ساعتان", "ساعات", "ساعة");
  return plural(Math.round(h / 24), "يوم", "يومان", "أيام", "يوماً");
}

type Forms = [one: string, two: string, few: string, many: string];

export const NOUNS = {
  book: ["كتاب واحد", "كتابان", "كتب", "كتاباً"],
  file: ["ملف واحد", "ملفان", "ملفات", "ملفاً"],
  chat: ["محادثة واحدة", "محادثتان", "محادثات", "محادثة"],
  message: ["رسالة واحدة", "رسالتان", "رسائل", "رسالة"],
  attempt: ["محاولة واحدة", "محاولتان", "محاولات", "محاولة"],
  time: ["مرة واحدة", "مرتان", "مرات", "مرة"],
  volume: ["جزء واحد", "جزءان", "أجزاء", "جزءاً"],
  folder: ["مجلد واحد", "مجلدان", "مجلدات", "مجلداً"],
} satisfies Record<string, Forms>;

/** Arabic counted noun: "ملف واحد", "ملفان", "٥ ملفات", "١٢ ملفاً". Zero uses the "many" form. */
export function count(n: number, forms: Forms): string {
  if (n === 0) return `${nf.format(0)} ${forms[3]}`;
  return plural(n, ...forms);
}

function plural(n: number, one: string, two: string, few: string, many: string): string {
  if (n === 1) return one;
  if (n === 2) return two;
  const mod100 = n % 100;
  if (mod100 >= 3 && mod100 <= 10) return `${nf.format(n)} ${few}`;
  return `${nf.format(n)} ${many}`;
}

/** Server timestamps are naive UTC ISO strings. */
export function parseUtc(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
}

const dateFmt = new Intl.DateTimeFormat("ar-EG", { dateStyle: "medium" });
const dateTimeFmt = new Intl.DateTimeFormat("ar-EG", { dateStyle: "medium", timeStyle: "short" });

export const date = (iso: string | null | undefined) => {
  const d = parseUtc(iso);
  return d ? dateFmt.format(d) : "—";
};

export const dateTime = (iso: string | null | undefined) => {
  const d = parseUtc(iso);
  return d ? dateTimeFmt.format(d) : "—";
};

const timeFmt = new Intl.DateTimeFormat("ar-EG", { timeStyle: "short" });
const dayFmt = new Intl.DateTimeFormat("ar-EG", { weekday: "long", day: "numeric", month: "long", year: "numeric" });

/** Time of day in the viewer's time zone: "٣:٤٥ م". */
export const time = (iso: string | null | undefined) => {
  const d = parseUtc(iso);
  return d ? timeFmt.format(d) : "";
};

/** The viewer's calendar day: "الأربعاء، ١ مايو ٢٠٢٤". */
export const day = (iso: string | null | undefined) => {
  const d = parseUtc(iso);
  return d ? dayFmt.format(d) : "";
};

/** Length of a recording: 125 -> "٢:٠٥". */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return digits(`${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);
}

export function ago(iso: string | null | undefined): string {
  const d = parseUtc(iso);
  if (!d) return "لم يُفحص بعد";
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return "الآن";
  return `قبل ${duration(s)}`;
}
