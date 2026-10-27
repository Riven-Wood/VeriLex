import type { TimelineItem, ToolItem, UserItem } from "./items";

/**
 * One instruction's whole lifecycle — tools, intermediate narration, the
 * final answer, and notices — folds into a single assistant bubble.
 */
export type TurnEntry =
  | { kind: "user"; item: UserItem }
  | {
      kind: "turn";
      turnId: string;
      items: TimelineItem[];
      /** Running or awaiting-approval turns stay unfolded. */
      isActive: boolean;
    };

function isBusyTool(item: TimelineItem): boolean {
  return item.kind === "tool"
    && (item.status === "running" || item.status === "awaiting_approval");
}

export function groupTurns(
  items: TimelineItem[],
  activeTurnId: string | null,
): TurnEntry[] {
  const entries: TurnEntry[] = [];
  let current: { turnId: string; items: TimelineItem[] } | null = null;

  const flush = () => {
    if (current === null) return;
    const isActive =
      current.turnId === activeTurnId
      || current.items.some(isBusyTool);
    entries.push({ kind: "turn", turnId: current.turnId, items: current.items, isActive });
    current = null;
  };

  for (const item of items) {
    if (item.kind === "user") {
      flush();
      entries.push({ kind: "user", item });
      continue;
    }
    const turnId: string = item.turnId ?? current?.turnId ?? "";
    if (current === null || turnId !== current.turnId) {
      flush();
      current = { turnId, items: [item] };
    } else {
      current.items.push(item);
    }
  }
  flush();
  return entries;
}

export function turnToolCount(items: TimelineItem[]): number {
  return items.filter((item): item is ToolItem => item.kind === "tool").length;
}
