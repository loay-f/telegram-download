import { useState } from "react";
import { Link } from "react-router-dom";
import { post } from "../lib/api";
import { bytes, date, stem } from "../lib/format";
import { CHAT_TYPE_LABEL, STATUS_LABEL } from "../lib/labels";
import { Button } from "./ui";

/** A file found by searching Telegram, and where it stands in the library. */
export interface Hit {
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

/** Adds a found file to the library and queues its download. */
export const downloadHit = (hit: Hit) =>
  post<Hit>("/api/search/download", { chat_id: hit.chat.id, msg_id: hit.msg_id });

export const sameHit = (a: Hit, b: Hit) => a.chat.id === b.chat.id && a.msg_id === b.msg_id;

export function FileHitRow({ hit: h, onPick }: { hit: Hit; onPick: () => Promise<void> }) {
  const type = CHAT_TYPE_LABEL[h.chat.type];
  return (
    <li className="px-4 py-3.5 sm:px-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span
          className="grid size-12 shrink-0 place-items-center rounded-full bg-accent-tint text-xs font-semibold text-accent-strong"
          dir="ltr"
        >
          {(h.ext ?? "؟").replace(".", "").toUpperCase().slice(0, 4)}
        </span>
        <div className="min-w-0 flex-1">
          <bdi className="block font-semibold [overflow-wrap:anywhere]" title={h.name}>
            {stem(h.name, h.ext)}
          </bdi>
          <p className="mt-0.5 text-sm text-ink-soft">
            {bytes(h.size)} ·{" "}
            <Link
              to={`/chats/${h.chat.id}?m=${h.msg_id}`}
              title="افتح الرسالة في محادثتها"
              className="text-accent underline-offset-4 hover:underline"
            >
              <bdi>{h.chat.title}</bdi>
            </Link>
            {type && <> ({type})</>} · {date(h.date)}
          </p>
          {h.caption && <p className="mt-0.5 line-clamp-2 text-sm text-ink-soft">{h.caption}</p>}
        </div>
        <HitAction hit={h} onPick={onPick} />
      </div>
    </li>
  );
}

function HitAction({ hit, onPick }: { hit: Hit; onPick: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  if (hit.has_file) {
    return (
      <Link
        to={`/read/${hit.doc_id}`}
        className="inline-flex min-h-11 items-center rounded-lg px-4 font-semibold text-accent hover:bg-accent-tint"
      >
        في مكتبتك
      </Link>
    );
  }
  if (hit.status === "pending" || hit.status === "downloading") {
    return <span className="inline-flex min-h-11 items-center text-accent">{STATUS_LABEL[hit.status]}</span>;
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
