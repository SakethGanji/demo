/**
 * ChecksList (UI-DESIGN §3.8): pass / warn / fail, each failure with its fix.
 * - `secrets_present`: an inline CreateSecretForm per missing key (key read-only, env = target).
 * - `app_calls_resolve`: apps + operations, `Create stubs` (`POST …/routes/stubs`).
 * - `handlers_exist` / `modules_parse`: links to the editor line.
 * - `stub_routes` / `breaking_vs_active`: amber, never block.
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, CircleCheck, CircleX } from "lucide-react";
import { useProjectCtx } from "@/features/project/ProjectContext";
import {
  useProjectApps,
  useRoutes,
} from "@/features/project/hooks/useProjectData";
import { CreateSecretForm } from "@/features/secrets/CreateSecretPopover";
import { useCreateSecrets } from "@/features/secrets/useSecretMutations";
import { routesApi } from "@/shared/api/endpoints";
import { errorMessage, isApiError } from "@/shared/api/http";
import { Input } from "@/shared/components/ui/input";
import { invalidate } from "@/shared/api/queryKeys";
import type { ReleaseCheck, Route } from "@/shared/api/types";
import { sortChecks } from "./checks";
import { Button } from "@/shared/components/ui/button";
import { cn } from "@/shared/lib/utils";

interface MissingCall {
  app_id: string;
  file: string;
  line: number;
  operation: string | null;
  method: string | null;
  path: string | null;
}

function titleOf(c: ReleaseCheck, env: string): string {
  const d = (c.details ?? {}) as Record<string, unknown>;
  const n = (k: string) =>
    Array.isArray(d[k]) ? (d[k] as unknown[]).length : 0;
  switch (c.id) {
    case "secrets_present":
      return c.status === "pass"
        ? `Secrets present in ${env}`
        : `Secrets missing in ${env}`;
    case "app_calls_resolve":
      return c.status === "pass"
        ? "Apps call existing routes"
        : "Apps call routes that do not exist";
    case "handlers_exist":
      return c.status === "pass"
        ? "Handlers exist"
        : "Routes without a handler";
    case "modules_parse":
      return c.status === "pass"
        ? "Modules parse"
        : "Modules with syntax errors";
    case "stub_routes":
      return c.status === "pass"
        ? "No stub routes"
        : `${n("routes")} stub route${n("routes") === 1 ? "" : "s"} (they return 501)`;
    case "breaking_vs_active":
      return c.status === "pass"
        ? "No breaking changes"
        : `${n("changes")} breaking change${n("changes") === 1 ? "" : "s"} vs the current ${env}`;
    case "subworkflows_resolved":
      return c.status === "pass" ? "Sub-workflow calls resolve" : "Sub-workflow calls that cannot run";
    case "subworkflow_contracts_compatible":
      return c.status === "pass" ? "Sub-workflow inputs and outputs fit" : "Sub-workflows that no longer fit their callers";
    default:
      return c.id.replace(/_/g, " ");
  }
}

function kebab(s: string) {
  return s
    .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .toLowerCase();
}

export function ChecksList({
  checks,
  targetEnv,
  onRerun,
  testId = "release-checks",
  className,
}: {
  checks: ReleaseCheck[];
  targetEnv: string;
  onRerun: () => void;
  testId?: string;
  className?: string;
}) {
  return (
    <div
      className={cn("flex flex-col gap-1.5", className)}
      data-testid={testId}
    >
      {sortChecks(checks).map((c) => (
        <CheckRow key={c.id} check={c} env={targetEnv} onRerun={onRerun} />
      ))}
    </div>
  );
}

function CheckRow({
  check: c,
  env,
  onRerun,
}: {
  check: ReleaseCheck;
  env: string;
  onRerun: () => void;
}) {
  const Icon =
    c.status === "pass"
      ? CircleCheck
      : c.status === "warn"
        ? CircleAlert
        : CircleX;
  return (
    <div
      className="flex items-start gap-2 text-[12px] leading-[18px]"
      data-testid={`check-${c.id}`}
      data-status={c.status}
    >
      <Icon
        className={cn(
          "mt-[3px] size-3 shrink-0 stroke-[2]",
          c.status === "pass" && "text-success",
          c.status === "warn" && "text-warning",
          c.status === "fail" && "text-destructive",
        )}
      />
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            c.status === "pass"
              ? "text-muted-foreground"
              : "font-medium text-foreground",
          )}
        >
          {titleOf(c, env)}
        </div>
        {c.status !== "pass" && (
          <CheckDetail check={c} env={env} onRerun={onRerun} />
        )}
      </div>
    </div>
  );
}

function CheckDetail({
  check: c,
  env,
  onRerun,
}: {
  check: ReleaseCheck;
  env: string;
  onRerun: () => void;
}) {
  const d = (c.details ?? {}) as Record<string, unknown>;
  switch (c.id) {
    case "secrets_present":
      return (
        <SecretsFix
          keys={(d.missing as string[]) ?? []}
          env={(d.environment as string) ?? env}
          onRerun={onRerun}
        />
      );
    case "app_calls_resolve":
      return (
        <AppCallsFix
          missing={(d.missing as MissingCall[]) ?? []}
          unversioned={(d.unversioned as string[]) ?? []}
          onRerun={onRerun}
        />
      );
    case "handlers_exist":
      return <HandlersFix routes={(d.routes as string[]) ?? []} />;
    case "modules_parse":
      return (
        <ParseFix
          errors={
            (d.errors as Array<{
              module: string;
              line: number | null;
              message: string;
            }>) ?? []
          }
        />
      );
    case "stub_routes":
      return (
        <Muted>
          {((d.routes as string[]) ?? []).join(" · ")} — stubs never block a
          release.
        </Muted>
      );
    case "breaking_vs_active": {
      const ch =
        (d.changes as Array<{
          kind: string;
          route: string;
          field?: string | null;
        }>) ?? [];
      return (
        <Muted>
          {ch
            .slice(0, 6)
            .map(
              (x) =>
                `${x.route} ${x.kind.replace(/_/g, " ")}${x.field ? ` (${x.field})` : ""}`,
            )
            .join(" · ")}
          {ch.length > 6 ? ` · +${ch.length - 6} more` : ""} — warns only.
        </Muted>
      );
    }
    default:
      return c.message ? <Muted>{c.message}</Muted> : null;
  }
}

function Muted({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-0.5 break-words text-xs leading-5 text-muted-foreground">
      {children}
    </div>
  );
}

function SecretsFix({
  keys,
  env,
  onRerun,
}: {
  keys: string[];
  env: string;
  onRerun: () => void;
}) {
  const [added, setAdded] = useState<string[]>([]);
  const allAdded = keys.length > 0 && keys.every((k) => added.includes(k));
  return (
    <div className="mt-1 flex flex-col gap-2">
      <Muted>
        {keys.map((k, i) => (
          <span key={k}>
            {i > 0 && ", "}
            <span className="font-mono">{k}</span>
          </span>
        ))}{" "}
        {keys.length === 1 ? "is" : "are"} used by your code and{" "}
        {keys.length === 1 ? "isn't" : "aren't"} set in {env}.
      </Muted>
      {keys.map((k) =>
        added.includes(k) ? (
          <div
            key={k}
            className="flex items-center gap-2 rounded-ctl bg-raise px-2.5 py-2 text-xs shadow-raise-sm"
            data-testid={`secret-fix-${k}-done`}
          >
            <span className="font-mono">{k}</span>
            <span className="inline-flex items-center gap-1 text-success-text">
              <Check className="size-3.5" /> added to {env}
            </span>
          </div>
        ) : (
          <SecretFixRow
            key={k}
            secretKey={k}
            env={env}
            onAdded={() => setAdded((a) => [...a, k])}
          />
        ),
      )}
      {allAdded && (
        <div>
          <Button size="xs" onClick={onRerun} data-testid="secret-fix-rerun">
            Re-run checks
          </Button>
        </div>
      )}
    </div>
  );
}

/**
 * One missing key: the compact inline row of `release.png` (key · value · Add to <env>), the
 * same one-POST-per-environment write as CreateSecretForm; "More options" opens that full form
 * (type, allowed hosts) with the key read-only and the target environment checked.
 */
function SecretFixRow({
  secretKey: k,
  env,
  onAdded,
}: {
  secretKey: string;
  env: string;
  onAdded: () => void;
}) {
  const { pid } = useProjectCtx();
  const [value, setValue] = useState("");
  const [full, setFull] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const create = useCreateSecrets(pid);
  const testId = `secret-fix-${k}-form`;
  if (full)
    return (
      <div
        className="rounded-ctl bg-raise p-2.5 shadow-raise-sm [&_form]:gap-2.5"
        data-testid={`secret-fix-${k}`}
      >
        <CreateSecretForm
          secretKey={k}
          keyReadOnly
          environments={[env]}
          testId={testId}
          onCreated={onAdded}
          onCancel={() => setFull(false)}
        />
      </div>
    );
  return (
    <div
      className="rounded-ctl bg-raise p-2 shadow-raise-sm"
      data-testid={`secret-fix-${k}`}
    >
      <form
        className="flex items-center gap-2"
        data-testid={testId}
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          create.mutate(
            { key: k, value, environments: [env], type: "secret" },
            {
              onSuccess: () => {
                setValue("");
                onAdded();
              },
              onError: (err) =>
                setError(
                  isApiError(err, undefined, 409)
                    ? `Already set in ${env}: re-run the checks`
                    : errorMessage(err),
                ),
            },
          );
        }}
      >
        <span className="shrink-0 font-mono text-xs">{k}</span>
        <Input
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="value"
          aria-label={`${k} value in ${env}`}
          className="h-7 min-w-0 flex-1 font-mono text-[12px]"
          data-testid={`${testId}-value`}
        />
        <button
          type="button"
          className="shrink-0 text-[11.5px] text-muted-foreground hover:text-foreground hover:underline"
          onClick={() => setFull(true)}
          data-testid={`${testId}-more`}
        >
          More options
        </button>
        <Button
          type="submit"
          size="xs"
          disabled={!value || create.isPending}
          data-testid={`${testId}-save`}
        >
          Add to {env}
        </Button>
      </form>
      {error && (
        <div
          className="mt-1 text-[11.5px] text-destructive"
          role="alert"
          data-testid={`${testId}-error`}
        >
          {error}
        </div>
      )}
    </div>
  );
}

function AppCallsFix({
  missing,
  unversioned,
  onRerun,
}: {
  missing: MissingCall[];
  unversioned: string[];
  onRerun: () => void;
}) {
  const { pid, slug } = useProjectCtx();
  const qc = useQueryClient();
  const apps = useProjectApps(pid);
  const name = (id: string) => apps.data?.find((a) => a.id === id)?.name ?? id;
  const byApp = new Map<string, MissingCall[]>();
  for (const m of missing)
    byApp.set(m.app_id, [...(byApp.get(m.app_id) ?? []), m]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string[]>([]);

  const stubSpec = (m: MissingCall) => {
    const op = m.operation ?? undefined;
    const method = (
      m.method ??
      (op && /^(get|list|fetch|find|search)/i.test(op) ? "GET" : "POST")
    ).toUpperCase();
    const path = m.path ?? `/${kebab(op ?? "endpoint")}`;
    return { method, path, ...(op ? { name: op } : {}) };
  };
  const createStubs = async (appId: string, calls: MissingCall[]) => {
    setBusy(appId);
    setError(null);
    try {
      const seen = new Set<string>();
      const routes = calls.map(stubSpec).filter((r) => {
        const k = `${r.method} ${r.path}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      });
      await routesApi.createStubs(pid, { app_id: appId, routes });
      setDone((d) => [...d, appId]);
      await invalidate.routesChanged(qc, pid);
      onRerun();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mt-1 flex flex-col gap-1.5">
      {[...byApp.entries()].map(([appId, calls]) => (
        <div
          key={appId}
          className="flex items-start gap-2 rounded-ctl bg-raise px-2.5 py-2 text-xs shadow-raise-sm"
          data-testid={`appcalls-${appId}`}
        >
          <div className="min-w-0 flex-1">
            <div className="font-medium">{name(appId)}</div>
            <div className="mt-0.5 break-words text-muted-foreground">
              {calls.map((m, i) => (
                <span key={i}>
                  {i > 0 && " · "}
                  <span className="font-mono text-foreground">
                    {m.operation ?? `${m.method ?? "GET"} ${m.path}`}
                  </span>
                  <span className="opacity-75">
                    {" "}
                    ({m.file}:{m.line})
                  </span>
                </span>
              ))}
            </div>
          </div>
          {done.includes(appId) ? (
            <span className="inline-flex items-center gap-1 text-success-text">
              <Check className="size-3.5" /> stubs created
            </span>
          ) : (
            <Button
              size="xs"
              variant="outline"
              disabled={busy === appId}
              onClick={() => void createStubs(appId, calls)}
              data-testid={`create-stubs-${appId}`}
            >
              Create stubs
            </Button>
          )}
        </div>
      ))}
      {unversioned.map((appId) => (
        <div
          key={appId}
          className="flex items-center gap-2 rounded-ctl bg-raise px-2.5 py-2 text-xs shadow-raise-sm"
          data-testid={`unversioned-${appId}`}
        >
          <span className="min-w-0 flex-1">
            <span className="font-medium">{name(appId)}</span>
            <span className="text-muted-foreground">
              {" "}
              has no saved code. Save the app once before releasing.
            </span>
          </span>
          <Link
            to="/p/$project/apps/$appId"
            params={{ project: slug, appId }}
            className="text-primary hover:underline"
          >
            Open builder
          </Link>
        </div>
      ))}
      {error && <div className="text-[11.5px] text-destructive">{error}</div>}
    </div>
  );
}

function editorLink(route: Route | undefined) {
  if (!route || route.handler.type !== "python") return null;
  return {
    module: route.handler.module_name ?? null,
    line: route.handler.line,
  };
}

function HandlersFix({ routes: bad }: { routes: string[] }) {
  const { pid, slug } = useProjectCtx();
  const routes = useRoutes(pid);
  return (
    <div className="mt-0.5 flex flex-col gap-0.5 text-xs">
      {bad.map((r) => {
        const [method, path] = r.split(" ");
        const route = routes.data?.find(
          (x) => x.method === method && x.path === path,
        );
        const link = editorLink(route);
        return (
          <div key={r} className="flex items-center gap-2">
            <span className="font-mono">{r}</span>
            {link?.module ? (
              <Link
                to="/p/$project/backend/$module"
                params={{ project: slug, module: link.module }}
                search={{ line: link.line, route: route?.name }}
                className="text-primary hover:underline"
                data-testid={`handler-link-${route?.id}`}
              >
                {link.module}.py{link.line ? `:${link.line}` : ""}
              </Link>
            ) : (
              <span className="text-muted-foreground">handler missing</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ParseFix({
  errors,
}: {
  errors: Array<{ module: string; line: number | null; message: string }>;
}) {
  const { slug } = useProjectCtx();
  return (
    <div className="mt-0.5 flex flex-col gap-0.5 text-xs">
      {errors.map((e, i) => (
        <div key={i} className="flex items-center gap-2">
          <Link
            to="/p/$project/backend/$module"
            params={{ project: slug, module: e.module }}
            search={{ line: e.line ?? undefined }}
            className="font-mono text-primary hover:underline"
            data-testid={`parse-link-${e.module}`}
          >
            {e.module}.py{e.line ? `:${e.line}` : ""}
          </Link>
          <span className="text-muted-foreground">{e.message}</span>
        </div>
      ))}
    </div>
  );
}
