/**
 * Settings · General (UI-DESIGN §3.7, review B7): name, description, slug. A slug change is
 * previewed with `PUT ?dry_run=true` (→ `affected_apps`), then saved, the published apps are
 * republished one by one, and the URL is replaced with the new slug.
 */
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AppWindow, Check, TriangleAlert, X } from "lucide-react";
import { useProjectCtx } from "@/features/project/ProjectContext";
import { projectsApi } from "@/shared/api/endpoints";
import { errorMessage } from "@/shared/api/http";
import { invalidate, qk } from "@/shared/api/queryKeys";
import { appsApi } from "@/shared/lib/api";
import { Badge } from "@/shared/components/ui/badge";
import { Checkbox } from "@/shared/components/ui/checkbox";
import { Input } from "@/shared/components/ui/input";
import { Spinner } from "@/shared/components/ui/spinner";
import { Textarea } from "@/shared/components/ui/textarea";
import { Callout, SectionHeader } from "@/shared/components/ui/surface";
import {
  InlineError,
  Kv,
  KvList,
  raisedField,
} from "./common";
import { useApplyProject } from "./projectHooks";
import { useProjectApps } from "@/features/project/hooks/useProjectData";
import { cn } from "@/shared/lib/utils";

/** What the dry run returns per app (engine shape + the spec's optional fields). */
interface AffectedApp {
  app_id: string;
  name: string;
  slug?: string | null;
  access?: string;
  published?: boolean;
  references_slug?: boolean;
}

type Progress = Record<
  string,
  {
    state: "pending" | "running" | "ok" | "error";
    message?: string;
    slug?: string | null;
  }
>;

function useDebounced<T>(v: T, ms: number): T {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

export function useGeneralForm(open: boolean) {
  const { project, pid, slug } = useProjectCtx();
  const qc = useQueryClient();
  const apply = useApplyProject();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const search = useRouterState({ select: (s) => s.location.search }) as Record<
    string,
    unknown
  >;

  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? "");
  const [newSlug, setNewSlug] = useState(project.slug);
  const [republish, setRepublish] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [renamed, setRenamed] = useState<{
    from: string;
    to: string;
    apps: AffectedApp[];
  } | null>(null);
  const appsList = useProjectApps(pid);

  // Re-seed from the server whenever the sheet opens (or another tab changed the project).
  useEffect(() => {
    if (!open) return;
    setName(project.name);
    setDescription(project.description ?? "");
    setNewSlug(project.slug);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, project.id, project.updated_at]);

  const slugChanged = newSlug.trim() !== project.slug && newSlug.trim() !== "";
  const dSlug = useDebounced(newSlug.trim(), 350);
  const preview = useQuery({
    queryKey: ["project", pid, "slug-preview", dSlug],
    queryFn: () => projectsApi.previewUpdate(pid, { slug: dSlug }),
    enabled: open && slugChanged && dSlug === newSlug.trim() && !saving,
    retry: false,
    staleTime: 10_000,
  });
  const affected = useMemo<AffectedApp[]>(() => {
    if (!slugChanged || !preview.data) return [];
    return ((preview.data.affected_apps ?? []) as AffectedApp[]).map((a) => {
      const known = appsList.data?.find((x) => x.id === a.app_id);
      return { ...a, slug: a.slug ?? known?.slug ?? null };
    });
  }, [slugChanged, preview.data, appsList.data]);
  const toRepublish = affected.filter((a) => a.published !== false);
  const nameDirty =
    name.trim() !== project.name ||
    (description || null) !== (project.description || null);
  const dirty = nameDirty || slugChanged;
  const slugError =
    slugChanged && preview.isError ? errorMessage(preview.error) : null;
  const previewPending =
    slugChanged && (dSlug !== newSlug.trim() || preview.isFetching);

  const save = async () => {
    setError(null);
    setSaving(true);
    const oldSlug = slug;
    try {
      const body: Record<string, unknown> = {};
      if (name.trim() !== project.name) body.name = name.trim();
      if ((description || null) !== (project.description || null))
        body.description = description || null;
      if (slugChanged) body.slug = newSlug.trim();
      const updated = await projectsApi.update(pid, body);
      // The new slug's cache entry first, so the layout resolves it without a spinner (D10).
      qc.setQueryData(qk.projectRef(updated.slug), updated);
      if (!slugChanged) {
        qc.setQueryData(qk.projectRef(oldSlug), updated);
        await apply(updated);
        setSaving(false);
        return;
      }
      const apps = republish ? toRepublish : [];
      setRenamed({ from: oldSlug, to: updated.slug, apps: affected });
      if (apps.length) {
        const init: Progress = {};
        for (const a of apps) init[a.app_id] = { state: "pending" };
        setProgress(init);
        for (const a of apps) {
          setProgress((p) => ({ ...p, [a.app_id]: { state: "running" } }));
          try {
            const detail = await appsApi.get(a.app_id);
            const res = await appsApi.publish(a.app_id, {
              slug: detail.slug ?? a.slug ?? undefined,
              access: detail.access ?? (a.access as never),
            });
            setProgress((p) => ({
              ...p,
              [a.app_id]: { state: "ok", slug: res.slug ?? detail.slug },
            }));
            void invalidate.appPublished(qc, pid, a.app_id);
          } catch (e) {
            setProgress((p) => ({
              ...p,
              [a.app_id]: { state: "error", message: errorMessage(e) },
            }));
          }
        }
      }
      // Navigate (replace) to the new slug, keeping the rest of the URL and the open sheet (§2.3).
      const rest = pathname.slice(`/p/${oldSlug}`.length);
      await navigate({
        to: `/p/${updated.slug}${rest}` as never,
        search: search as never,
        replace: true,
      });
      qc.removeQueries({ queryKey: qk.projectRef(oldSlug), exact: true });
      await apply(updated);
      await invalidate.routesChanged(qc, pid);
      await invalidate.appsChanged(qc, pid);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    setName(project.name);
    setDescription(project.description ?? "");
    setNewSlug(project.slug);
    setError(null);
    setProgress(null);
    setRenamed(null);
  };

  return {
    project,
    name,
    setName,
    description,
    setDescription,
    newSlug,
    setNewSlug,
    republish,
    setRepublish,
    slugChanged,
    affected,
    toRepublish,
    slugError,
    previewPending,
    dirty,
    saving,
    error,
    progress,
    renamed,
    save,
    reset,
    canSave: dirty && !saving && !slugError && !previewPending && !!name.trim(),
    saveLabel: slugChanged
      ? republish && toRepublish.length
        ? `Rename and republish ${toRepublish.length} app${toRepublish.length === 1 ? "" : "s"}`
        : "Rename project"
      : "Save changes",
  };
}

export type GeneralForm = ReturnType<typeof useGeneralForm>;

export function GeneralSection({ form }: { form: GeneralForm }) {
  const f = form;
  const isDefault = f.project.id === "proj_default";
  return (
    <div className="flex flex-col gap-3" data-testid="settings-general">
      <SectionHeader title="General" />
      <KvList>
        <Kv label="Name">
          <Input
            value={f.name}
            onChange={(e) => f.setName(e.target.value)}
            className={raisedField}
            data-testid="settings-name"
          />
        </Kv>
        <Kv label="Description" top>
          <Textarea
            value={f.description}
            onChange={(e) => f.setDescription(e.target.value)}
            rows={2}
            className={cn("min-h-14 resize-none", raisedField)}
            placeholder="What this project's backend does"
            data-testid="settings-description"
          />
        </Kv>
        <Kv label="Slug" top>
          <div
            className={cn(
              "field flex h-8 items-center px-2.5 font-mono text-[12.5px] focus-within:shadow-focus!",
              raisedField,
              f.slugError && "shadow-[0_0_0_2px_var(--destructive-soft)]!",
            )}
          >
            <span className="text-muted-foreground">/api/p/</span>
            <input
              value={f.newSlug}
              disabled={isDefault}
              onChange={(e) =>
                f.setNewSlug(
                  e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"),
                )
              }
              className="h-full min-w-0 flex-1 bg-transparent pl-1 outline-none disabled:opacity-60"
              spellCheck={false}
              data-testid="settings-slug"
            />
            {f.previewPending && (
              <Spinner className="size-3.5 text-muted-foreground" />
            )}
          </div>
          {f.slugError ? (
            <div
              className="text-[12px] leading-4 text-destructive"
              role="alert"
              data-testid="settings-slug-error"
            >
              {f.slugError}
            </div>
          ) : (
            <div className="text-[12px] leading-4 text-muted-foreground">
              {isDefault
                ? "The Default project's slug cannot change."
                : "Changes the URL of every route and every published app."}
            </div>
          )}
        </Kv>
      </KvList>

      {f.slugChanged && !f.slugError && !f.previewPending && !f.saving && (
        <SlugConfirm form={f} />
      )}
      {f.renamed && (f.saving || !f.slugChanged) && <RenameResult form={f} />}
      {f.error && (
        <InlineError testId="settings-general-error">{f.error}</InlineError>
      )}
    </div>
  );
}

function SlugConfirm({ form: f }: { form: GeneralForm }) {
  const n = f.toRepublish.length;
  const oldBase = `/api/p/${f.project.slug}`;
  if (!f.affected.length)
    return (
      <Callout tone="info" className="items-start" data-testid="slug-confirm">
        <span>
          No published app calls <span className="font-mono">{oldBase}</span>.
          Routes move to <span className="font-mono">/api/p/{f.newSlug}</span>;
          the old base stops resolving.
        </span>
      </Callout>
    );
  return (
    <Callout
      tone="warn"
      icon={<TriangleAlert />}
      className="items-start"
      data-testid="slug-confirm"
    >
      <b className="font-semibold">Published apps break until republished</b>
      <div className="mt-1">
        They call <span className="font-mono">{oldBase}</span>, which stops
        resolving after the rename.
      </div>
      <AppRows apps={f.affected} progress={f.progress} />
      {n > 0 && (
        <label className="mt-2.5 flex items-center gap-2 text-[13px] text-foreground">
          <Checkbox
            checked={f.republish}
            onCheckedChange={(c) => f.setRepublish(!!c)}
            data-testid="slug-republish"
          />
          Republish {n} app{n === 1 ? "" : "s"} right after renaming
        </label>
      )}
      <div className="mt-1.5 text-[11.5px] leading-4 text-muted-foreground">
        Hand-written <span className="font-mono">fetch('{oldBase}/…')</span>{" "}
        calls are not rewritten; the AI and the typed client use the new slug.
      </div>
    </Callout>
  );
}

function AppRows({
  apps,
  progress,
}: {
  apps: AffectedApp[];
  progress: Progress | null;
}) {
  return (
    <div className="mt-2.5 flex flex-col gap-1.5 text-foreground">
      {apps.map((a) => {
        const p = progress?.[a.app_id];
        return (
          <div
            key={a.app_id}
            className="flex items-center gap-2 rounded-ctl bg-raise px-2.5 py-2 text-[12.5px] shadow-raise-sm"
            data-testid={`slug-affected-${a.app_id}`}
            data-state={p?.state}
          >
            <AppWindow className="size-3.5 text-hue-apps" />
            <span className="truncate">{a.name}</span>
            {a.slug && (
              <span className="truncate font-mono text-[11.5px] text-muted-foreground">
                /a/{a.slug}
              </span>
            )}
            <div className="flex-1" />
            {p?.state === "running" && <Spinner className="size-3.5" />}
            {p?.state === "pending" && (
              <span className="text-xs text-muted-foreground">waiting</span>
            )}
            {p?.state === "ok" && (
              <span className="inline-flex items-center gap-1 text-xs text-success-text">
                <Check className="size-3.5" /> republished
              </span>
            )}
            {p?.state === "error" && (
              <span
                className="inline-flex min-w-0 items-center gap-1 truncate text-xs text-destructive"
                title={p.message}
              >
                <X className="size-3.5" /> {p.message}
              </span>
            )}
            {!p &&
              (a.published === false ? (
                <Badge variant="neutral">draft</Badge>
              ) : (
                <Badge variant="success">published</Badge>
              ))}
          </div>
        );
      })}
    </div>
  );
}

function RenameResult({ form: f }: { form: GeneralForm }) {
  const r = f.renamed!;
  const failed = Object.values(f.progress ?? {}).some(
    (p) => p.state === "error",
  );
  return (
    <Callout
      tone={failed ? "warn" : "info"}
      icon={f.saving ? <Spinner /> : <Check className="text-success-text" />}
      className="items-start"
      data-testid="slug-renamed"
      data-failed={failed || undefined}
    >
      <span>
        {f.saving ? "Renaming" : "Renamed"}{" "}
        <span className="font-mono">/api/p/{r.from}</span> →{" "}
        <span className="font-mono">/api/p/{r.to}</span>
      </span>
      {r.apps.length > 0 && <AppRows apps={r.apps} progress={f.progress} />}
      {failed && (
        <div className="mt-2 text-[11.5px] text-destructive">
          Some apps failed to republish; open them in the builder and publish
          again.
        </div>
      )}
    </Callout>
  );
}
