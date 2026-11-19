/** `text` cut down to the part around the first word of `query` found in
 * it, with that word marked. */
export function Highlight({ text, query }: { text: string; query: string }) {
  const lower = text.toLocaleLowerCase();
  let at = -1;
  let length = 0;
  for (const word of query.toLocaleLowerCase().split(" ")) {
    const i = word ? lower.indexOf(word) : -1;
    if (i !== -1 && (at === -1 || i < at)) [at, length] = [i, word.length];
  }
  // Telegram also matches other forms of a word: then there is nothing to mark.
  if (at === -1) return <>{text.slice(0, 160)}</>;
  const start = Math.max(0, at - 40);
  return (
    <>
      {start > 0 && "…"}
      {text.slice(start, at)}
      <mark className="rounded-sm bg-warn-tint px-0.5 font-semibold text-ink">{text.slice(at, at + length)}</mark>
      {text.slice(at + length, at + length + 120)}
    </>
  );
}
