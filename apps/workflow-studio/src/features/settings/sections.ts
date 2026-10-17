export const SECTIONS = [
  { key: "general", label: "General" },
  { key: "environments", label: "Environments" },
  { key: "egress", label: "Egress" },
  { key: "keys", label: "API keys" },
  { key: "developer", label: "Developer" },
  { key: "danger", label: "Danger zone" },
] as const;
export type SectionKey = (typeof SECTIONS)[number]["key"];

export function isSection(s: string | undefined): s is SectionKey {
  return !!s && SECTIONS.some((x) => x.key === s);
}
