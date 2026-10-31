export interface ProjectSidebarState {
  pinned: string[];
  hidden: string[];
  groups: Record<string, string>;
  order: string[];
  groupOrder: string[];
}

const key = 'verilex:project-sidebar:v1';
export function readProjectSidebarState(): ProjectSidebarState {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '{}');
    return {
      groupOrder: Array.isArray(value.groupOrder) ? value.groupOrder.filter((x: unknown) => typeof x === 'string') : [],
      order: Array.isArray(value.order) ? value.order.filter((x: unknown) => typeof x === 'string') : [],
      pinned: Array.isArray(value.pinned) ? value.pinned.filter((x: unknown) => typeof x === 'string') : [],
      hidden: Array.isArray(value.hidden) ? value.hidden.filter((x: unknown) => typeof x === 'string') : [],
      groups: value.groups && typeof value.groups === 'object' && !Array.isArray(value.groups) ? Object.fromEntries(Object.entries(value.groups).filter((entry): entry is [string, string] => typeof entry[1] === 'string')) : {},
    };
  } catch { return {pinned: [], hidden: [], groups: {}, order: [], groupOrder: []}; }
}
export function writeProjectSidebarState(state: ProjectSidebarState): void {
  localStorage.setItem(key, JSON.stringify(state));
}

// Older versions only hid removed projects. Revoke those remaining grants once.
export async function clearPreviouslyRemovedProjects(
  list: () => Promise<{id:string}[]>, revoke: (id:string) => Promise<void>,
): Promise<boolean> {
  const hidden = readProjectSidebarState().hidden;
  if (!hidden.length) return true;
  const existing = new Set((await list()).map(item=>item.id));
  let failed = false;
  for (const id of hidden) {
    if (!readProjectSidebarState().hidden.includes(id)) continue;
    try {
      if (existing.has(id)) await revoke(id);
      const current = readProjectSidebarState();
      writeProjectSidebarState({...current, hidden:current.hidden.filter(item=>item!==id)});
    } catch { failed = true; }
  }
  return !failed;
}
