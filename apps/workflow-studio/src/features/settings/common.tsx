/**
 * Small pieces shared by the Settings sheet sections (UI-DESIGN §3.7).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useProjectCtx } from "@/features/project/ProjectContext";
import { projectsApi } from "@/shared/api/endpoints";
import { invalidate, qk } from "@/shared/api/queryKeys";
import type { Project, ProjectUpdate } from "@/shared/api/types";
import { Input } from "@/shared/components/ui/input";
import { CopyButton } from "@/shared/components/ui/snippet";
import { Well } from "@/shared/components/ui/surface";
import { cn } from "@/shared/lib/utils";

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

export { SectionHeader } from "@/shared/components/ui/surface";

/** A control that sits on a well is raised, not recessed (DESIGN §4.3): the well and the field share a fill in a sheet. */
export const raisedField =
  "bg-raise! shadow-raise-sm! focus-visible:shadow-focus!";

/** Key/value rows in a well (the kit's `kit-kv`): a muted key, then the value, optional trailing content. */
export function KvList({
  children,
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <Well className={cn("flex flex-col", className)} {...props}>
      {children}
    </Well>
  );
}

export function Kv({
  label,
  children,
  className,
  danger,
  top,
  ...props
}: { label: React.ReactNode; danger?: boolean; top?: boolean } & Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "children"
> & { children?: React.ReactNode }) {
  return (
    <div
      className={cn(
        "flex min-h-11 gap-3 border-b border-hairline px-4 py-2 text-[13px] last:border-0",
        top ? "items-start" : "items-center",
        className,
      )}
      {...props}
    >
      <span
        className={cn(
          "w-[104px] shrink-0 text-[12.5px]",
          top && "pt-1.5",
          danger ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {label}
      </span>
      <div className="flex min-w-0 flex-1 flex-col justify-center gap-1.5">
        {children}
      </div>
    </div>
  );
}

/** A secret value shown exactly once, with a Copy button ("shown once, copy it now"). */
export function OnceBox({
  icon,
  title,
  value,
  note = "shown once, copy it now",
  footer,
  testId,
}: {
  icon?: React.ReactNode;
  title: React.ReactNode;
  value: string;
  note?: string;
  footer?: React.ReactNode;
  testId?: string;
}) {
  return (
    <Well className="p-3" data-testid={testId}>
      <div className="flex flex-wrap items-center gap-2 [&_svg]:size-3.5 [&_svg]:text-hue-secrets">
        {icon}
        <span className="font-semibold">{title}</span>
        <span className="text-xs text-muted-foreground">{note}</span>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <Input
          readOnly
          value={value}
          onFocus={(e) => e.currentTarget.select()}
          className="min-w-0 flex-1 font-mono text-[11.5px]"
          data-testid={testId ? `${testId}-value` : undefined}
        />
        <CopyButton
          text={value}
          what="key"
          testId={testId ? `${testId}-copy` : undefined}
        />
      </div>
      {footer && (
        <div className="mt-2 text-[11.5px] leading-4 text-muted-foreground">
          {footer}
        </div>
      )}
    </Well>
  );
}

/** Writes the updated project into every cache that holds it (UI-DESIGN D10: keys use the id). */
export function useApplyProject() {
  const qc = useQueryClient();
  return async (p: Project) => {
    qc.setQueryData(qk.projectRef(p.slug), p);
    qc.setQueryData(qk.projectRef(p.id), p);
    await invalidate.projectUpdated(qc, p.id);
  };
}

/** `PUT /api/projects/{P}`; the caller renders errors inline. Does not handle slug renames. */
export function useProjectUpdate() {
  const { pid, slug } = useProjectCtx();
  const qc = useQueryClient();
  const apply = useApplyProject();
  return useMutation({
    mutationFn: (b: ProjectUpdate) => projectsApi.update(pid, b),
    onSuccess: async (p) => {
      if (p.slug === slug) qc.setQueryData(qk.projectRef(slug), p);
      await apply(p);
    },
  });
}

export function InlineError({
  children,
  testId,
  className,
}: {
  children: React.ReactNode;
  testId?: string;
  className?: string;
}) {
  return (
    <div
      className={cn("text-[11.5px] leading-4 text-destructive", className)}
      role="alert"
      data-testid={testId}
    >
      {children}
    </div>
  );
}
