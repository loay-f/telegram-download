/** The kinds of file a person thinks in ("pictures", "video"), each standing
 * for a list of file extensions. Keep in step with settings_store.py, whose
 * defaults are all of these. */
export interface FileFormat {
  label: string;
  /** Without the dot; several when they are the same thing to a person. */
  exts: string[];
}

export interface FileKind {
  key: "books" | "images" | "video" | "audio" | "archives";
  title: string;
  hint: string;
  formats: FileFormat[];
}

export const FILE_KINDS: FileKind[] = [
  {
    key: "books",
    title: "الكتب والمستندات",
    hint: "ملفات PDF و EPUB و Word والنصوص",
    formats: [
      { label: "PDF", exts: ["pdf"] },
      { label: "EPUB", exts: ["epub"] },
      { label: "DjVu", exts: ["djvu"] },
      { label: "كندل", exts: ["mobi", "azw3"] },
      { label: "Word", exts: ["docx", "doc"] },
      { label: "نص", exts: ["txt", "md", "rtf"] },
      { label: "ODT", exts: ["odt"] },
    ],
  },
  {
    key: "images",
    title: "الصور",
    hint: "الصور المنشورة في المحادثات وملفات الصور",
    formats: [
      { label: "JPG", exts: ["jpg", "jpeg"] },
      { label: "PNG", exts: ["png"] },
      { label: "WebP", exts: ["webp"] },
      { label: "GIF", exts: ["gif"] },
      { label: "HEIC (آيفون)", exts: ["heic", "heif"] },
      { label: "BMP", exts: ["bmp"] },
      { label: "TIFF", exts: ["tif", "tiff"] },
    ],
  },
  {
    key: "video",
    title: "الفيديو",
    hint: "المقاطع المنشورة وملفات الفيديو — قد تكون كبيرة الحجم",
    formats: [
      { label: "MP4", exts: ["mp4", "m4v"] },
      { label: "MKV", exts: ["mkv"] },
      { label: "MOV", exts: ["mov"] },
      { label: "AVI", exts: ["avi"] },
      { label: "WebM", exts: ["webm"] },
      { label: "3GP", exts: ["3gp"] },
      { label: "WMV", exts: ["wmv"] },
      { label: "FLV", exts: ["flv"] },
      { label: "MPEG", exts: ["mpg", "mpeg"] },
    ],
  },
  {
    key: "audio",
    title: "الصوتيات",
    hint: "الدروس والتلاوات والمقاطع الصوتية",
    formats: [
      { label: "MP3", exts: ["mp3"] },
      { label: "M4A", exts: ["m4a"] },
      { label: "OGG", exts: ["ogg", "oga", "opus"] },
      { label: "WAV", exts: ["wav"] },
      { label: "FLAC", exts: ["flac"] },
      { label: "AAC", exts: ["aac"] },
      { label: "WMA", exts: ["wma"] },
      { label: "AMR", exts: ["amr"] },
    ],
  },
  {
    key: "archives",
    title: "الملفات المضغوطة",
    hint: "مجموعات ملفات في ملف واحد: ZIP و RAR و 7z",
    formats: [
      { label: "ZIP", exts: ["zip"] },
      { label: "RAR", exts: ["rar"] },
      { label: "7z", exts: ["7z"] },
    ],
  },
];

const KNOWN = new Set(FILE_KINDS.flatMap((k) => k.formats.flatMap((f) => f.exts)));

/** "pdf, .EPUB،jpg" -> ["pdf", "epub", "jpg"] */
export function parseExts(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[,،;\s]+/)) {
    const ext = part.trim().toLowerCase().replace(/^\.+/, "");
    if (ext) seen.add(ext);
  }
  return [...seen];
}

/** The extensions that belong to none of the kinds above. */
export const otherExts = (exts: Iterable<string>) => [...exts].filter((e) => !KNOWN.has(e));

/** How a downloaded file can be opened inside the app, by its extension
 * (with the dot): read as a book, or shown / played by the browser. */
const VIEWERS: Record<string, "book" | "image" | "video" | "audio"> = {};
for (const e of ["pdf", "epub", "txt"]) VIEWERS[`.${e}`] = "book";
for (const e of ["jpg", "jpeg", "png", "webp", "gif", "bmp"]) VIEWERS[`.${e}`] = "image";
for (const e of ["mp4", "m4v", "webm"]) VIEWERS[`.${e}`] = "video";
for (const e of ["mp3", "m4a", "ogg", "oga", "opus", "wav", "flac", "aac"]) VIEWERS[`.${e}`] = "audio";

export const viewerFor = (ext: string | null | undefined) => (ext ? VIEWERS[ext.toLowerCase()] ?? null : null);

export const OPEN_LABEL = { book: "قراءة", image: "عرض", video: "تشغيل", audio: "استماع" } as const;

/** Sizes offered for "don't download files larger than", in megabytes. */
export const SIZE_LIMITS: { mb: number; label: string }[] = [
  { mb: 0, label: "بلا حد" },
  { mb: 50, label: "٥٠ ميغابايت" },
  { mb: 200, label: "٢٠٠ ميغابايت" },
  { mb: 500, label: "٥٠٠ ميغابايت" },
  { mb: 1024, label: "١ غيغابايت" },
  { mb: 2048, label: "٢ غيغابايت" },
];
