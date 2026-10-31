export interface SearchSettings {
  kind: "online" | "plugin" | "knowledge";
  serverId: string | null;
  skills: string[];
  knowledgeIds: string[];
  valid: boolean;
}
