import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useProjectCtx } from "@/features/project/ProjectContext";
import { projectsApi } from "@/shared/api/endpoints";
import { invalidate, qk } from "@/shared/api/queryKeys";
import type { Project, ProjectUpdate } from "@/shared/api/types";

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
