import type { TimelineItem } from "./items";
import { lawResultSummary } from "./lawResults";
import { clauseOrdinal } from "./lawClause";

/** Sort the result-card slots within one turn; conversation events keep their positions. */
export function orderLegalCards(items: TimelineItem[]): TimelineItem[] {
  const groups = new Map<string, number>();
  const cards = items.flatMap((item, index) => {
    if (item.kind !== "tool" || item.status !== "succeeded") return [];
    const read = lawResultSummary(item);
    if (read?.kind !== "law_read") return [];
    const version = JSON.stringify([read.bbbs, read.title, read.gbrq, read.sxrq]);
    if (!groups.has(version)) groups.set(version, groups.size);
    return [{ item, index, group: groups.get(version)!, order: clauseOrdinal(read.clause) }];
  });
  const positions = new Set(cards.map((card) => card.index));
  cards.sort((a, b) => a.group - b.group
    || (a.order?.[0] ?? Infinity) - (b.order?.[0] ?? Infinity)
    || (a.order?.[1] ?? Infinity) - (b.order?.[1] ?? Infinity)
    || a.index - b.index);
  let next = 0;
  return items.map((item, index) => positions.has(index) ? cards[next++].item : item);
}
