/**
 * Settings · Danger (UI-DESIGN §3.7): delete the project with type-to-confirm. `409
 * project_not_empty` lists what blocks it inline; success → `/projects`.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { useProjectCtx } from "@/features/project/ProjectContext";
import { projectsApi } from "@/shared/api/endpoints";
import { errorMessage, isApiError } from "@/shared/api/http";
import { invalidate, qk } from "@/shared/api/queryKeys";
import { Button } from "@/shared/components/ui/button";
import { Input } from "@/shared/components/ui/input";
import { toast } from "sonner";
import { Callout } from "@/shared/components/ui/surface";
import { cn } from "@/shared/lib/utils";
import { InlineError, Kv, KvList, raisedField, SectionHeader } from "./common";

const LABELS: Record<string, [string, string]> = {
  routes: ["route", "routes"],
  apps: ["app", "apps"],
  workflows: ["workflow", "workflows"],
  tables: ["table", "tables"],
  modules: ["module", "modules"],
};

export function DangerSection() {
  const { project, pid, slug } = useProjectCtx();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [confirm, setConfirm] = useState("");
  const del = useMutation({
    mutationFn: () => projectsApi.remove(pid),
    onSuccess: async () => {
      await navigate({ to: "/projects", replace: true });
      qc.removeQueries({ queryKey: qk.projectRef(slug) });
      qc.removeQueries({ queryKey: qk.projectRef(pid) });
      await invalidate.projectDeleted(qc, pid);
      toast.success(`Deleted ${project.name}`);
    },
  });
  const blockers = isApiError(del.error, "project_not_empty")
    ? Object.entries(
        (del.error.details.counts as Record<string, number> | undefined) ?? {},
      ).filter(([, n]) => n > 0)
    : [];
  const isDefault = project.id === "proj_default";
  const errRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (del.isError)
      errRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [del.isError, del.failureCount]);

  return (
    <div className="flex flex-col gap-3" data-testid="settings-danger">
      <SectionHeader title="Danger zone" />
      <KvList>
        <Kv label="Delete project" danger top>
          <div className="text-xs leading-5 text-muted-foreground">
            {isDefault
              ? "The Default project cannot be deleted."
              : "Only an empty project can be deleted: remove its routes, modules, tables, apps and workflows first. Secrets, API keys and releases are deleted with it. This cannot be undone."}
          </div>
          {!isDefault && (
            <div className="flex items-center gap-2">
              <Input
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder={`type ${project.slug} to confirm`}
                className={cn(
                  "h-7 flex-1 font-mono text-[12.5px]",
                  raisedField,
                )}
                data-testid="danger-confirm"
              />
              <Button
                variant="destructive"
                size="sm"
                disabled={confirm !== project.slug || del.isPending}
                onClick={() => del.mutate()}
                data-testid="danger-delete"
              >
                Delete project
              </Button>
            </div>
          )}
          {del.isError &&
            (blockers.length ? (
              <Callout
                tone="warn"
                icon={<TriangleAlert />}
                className="items-start"
                role="alert"
                data-testid="project-not-empty"
              >
                <div className="font-medium">{errorMessage(del.error)}</div>
                <ul className="mt-1 list-disc pl-5">
                  {blockers.map(([k, n]) => (
                    <li key={k} data-testid={`not-empty-${k}`}>
                      {n} {(LABELS[k] ?? [k, k])[n === 1 ? 0 : 1]}
                    </li>
                  ))}
                </ul>
              </Callout>
            ) : (
              <InlineError
                testId={
                  isApiError(del.error, "project_not_empty")
                    ? "project-not-empty"
                    : "danger-error"
                }
              >
                {errorMessage(del.error)}
              </InlineError>
            ))}
          <div ref={errRef} />
        </Kv>
      </KvList>
    </div>
  );
}
