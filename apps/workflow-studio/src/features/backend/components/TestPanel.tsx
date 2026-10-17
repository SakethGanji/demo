/**
 * Test panel (UI-DESIGN §4.7): route picker, Params/Query/Body/Headers, Send ⌘⏎, response,
 * trace, recent tests. Saved + unchanged routes use `POST …/routes/{R}/test`; dirty or draft
 * code uses `POST …/modules/run-draft` (§10.2). The environment is always the project's
 * default environment.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  FileJson2,
  History,
  Send,
  Table2,
} from "lucide-react";
import { toast } from "sonner";
import { useProjectCtx } from "@/features/project/ProjectContext";
import { modulesApi, routesApi, tablesApi } from "@/shared/api/endpoints";
import { errorMessage, isApiError } from "@/shared/api/http";
import { invalidate } from "@/shared/api/queryKeys";
import type {
  BrokerCall,
  Column,
  RouteTestResult,
  RunDraftResult,
} from "@/shared/api/types";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { Input } from "@/shared/components/ui/input";
import { Kbd } from "@/shared/components/ui/kbd";
import { Roll } from "@/shared/components/ui/roll";
import { Segmented } from "@/shared/components/ui/segmented";
import { RoutePicker } from "@/shared/components/ui/search-menu";
import { Spinner } from "@/shared/components/ui/spinner";
import { Callout, Well } from "@/shared/components/ui/surface";
import { Textarea } from "@/shared/components/ui/textarea";
import {
  Tip,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/shared/components/ui/tooltip";
import { RunErrorBox } from "@/features/runs/components/RunErrorBox";
import { CreateSecretPopover } from "@/features/secrets/CreateSecretPopover";
import { formatMs, timeAgo } from "@/features/project/lib/format";
import { cn } from "@/shared/lib/utils";
import { JsonBlock, jsonText } from "./JsonBlock";
import type { RecentTest } from "../docStore";
import { RouteSettingsButton } from "./RouteSettings";
import { RequestPath } from "../ui/RequestPath";
import { TraceView } from "../ui/TraceView";

export interface TestRoute {
  method: string;
  path: string;
  name: string;
  draft: boolean;
  routeId: string | null;
  auth?: string;
}

type ReqTab = "params" | "query" | "body" | "headers";
type ResTab = "body" | "headers" | "schema";
type KV = Array<{ k: string; v: string }>;

interface ReqState {
  params: Record<string, string>;
  query: KV;
  headers: KV;
  body: string;
}

const kvToObj = (kv: KV) =>
  Object.fromEntries(
    kv.filter((x) => x.k.trim()).map((x) => [x.k.trim(), x.v]),
  );
const objToKv = (o: Record<string, string>): KV => [
  ...Object.entries(o).map(([k, v]) => ({ k, v })),
  { k: "", v: "" },
];

function pathParams(path: string): string[] {
  return [...path.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
}

function statusTone(s: number) {
  if (s >= 200 && s < 300) return "text-success-text bg-success-soft";
  if (s >= 400 && s < 500) return "text-warning-text bg-warning-soft";
  return "text-destructive bg-destructive-soft";
}

export function StatusPill({
  status,
  small,
}: {
  status: number;
  small?: boolean;
}) {
  return (
    <span
      data-testid="status-pill"
      data-status={status}
      className={cn(
        "inline-flex items-center rounded-chip font-mono font-semibold tabular-nums",
        small ? "h-[18px] px-1.5 text-[10.5px]" : "h-[22px] px-2 text-[11.5px]",
        statusTone(status),
      )}
    >
      {status}
    </span>
  );
}

export function TestPanel({
  routes,
  selected,
  onSelect,
  useDraftRun,
  source,
  moduleName,
  moduleId,
  virtualTables,
  draftTables,
  dirty,
  sandboxAvailable,
  narrow,
  onGoToLine,
  recentTests,
  onRecord,
}: {
  routes: TestRoute[];
  selected: string | null;
  onSelect: (name: string) => void;
  /** run the editor text through run-draft instead of the saved route */
  useDraftRun: boolean;
  source: string;
  moduleName: string;
  moduleId: string | null;
  /** declared tables that don't exist yet (reads empty, writes table_not_found) */
  virtualTables: string[];
  /** declared columns per draft table, for "Create table now" */
  draftTables: Record<string, Column[]>;
  dirty: boolean;
  sandboxAvailable: boolean;
  narrow: boolean;
  onGoToLine: (line: number) => void;
  recentTests: RecentTest[];
  onRecord: (t: RecentTest) => void;
}) {
  const { project, pid, slug } = useProjectCtx();
  const qc = useQueryClient();
  const env = project.default_environment;
  const route = routes.find((r) => r.name === selected) ?? routes[0] ?? null;
  const [reqTab, setReqTab] = useState<ReqTab>("query");
  const [resTab, setResTab] = useState<ResTab>("body");
  const [reqs, setReqs] = useState<Record<string, ReqState>>({});
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{
    res: RouteTestResult | RunDraftResult;
    draft: boolean;
    route: TestRoute;
  } | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const key = route ? `${route.method} ${route.path}` : "";
  const req: ReqState = reqs[key] ?? {
    params: Object.fromEntries(
      (route ? pathParams(route.path) : []).map((p) => [p, ""]),
    ),
    query: [{ k: "", v: "" }],
    headers: [{ k: "", v: "" }],
    body:
      route && ["POST", "PUT", "PATCH"].includes(route.method)
        ? "{\n  \n}"
        : "",
  };
  const setReq = (patch: Partial<ReqState>) =>
    setReqs((s) => ({ ...s, [key]: { ...req, ...patch } }));

  useEffect(() => {
    if (route && ["POST", "PUT", "PATCH"].includes(route.method))
      setReqTab("body");
    else if (route && pathParams(route.path).length) setReqTab("params");
    else setReqTab("query");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const missingTablesNote = virtualTables.length > 0;
  const authNote = useDraftRun && route && route.auth && route.auth !== "none";

  const send = async () => {
    if (!route || sending || !sandboxAvailable) return;
    setSendError(null);
    let body: unknown = undefined;
    if (req.body.trim()) {
      try {
        body = JSON.parse(req.body);
      } catch {
        setSendError("Body is not valid JSON");
        setReqTab("body");
        return;
      }
    }
    const params = req.params;
    for (const p of pathParams(route.path)) {
      if (!params[p]) {
        setSendError(`Fill the path parameter "${p}"`);
        setReqTab("params");
        return;
      }
    }
    const concretePath = route.path.replace(/\{(\w+)\}/g, (_, p: string) =>
      encodeURIComponent(params[p] ?? ""),
    );
    const query = kvToObj(req.query);
    const headers = kvToObj(req.headers);
    abortRef.current?.abort();
    const ctl = new AbortController();
    abortRef.current = ctl;
    setSending(true);
    try {
      let res: RouteTestResult | RunDraftResult;
      const draft = useDraftRun || !route.routeId;
      if (draft) {
        res = await modulesApi.runDraft(
          pid,
          {
            source,
            module_name: moduleName,
            module_id: moduleId,
            method: route.method,
            path: concretePath,
            query,
            headers,
            body: body ?? null,
            virtual_tables: virtualTables,
          },
          ctl.signal,
        );
      } else {
        res = await routesApi.test(pid, route.routeId!, {
          params,
          query,
          headers,
          body: body ?? null,
          environment: env,
          apply_schemas: false,
        });
      }
      setResult({ res, draft, route });
      setResTab("body");
      onRecord({
        id: `${Date.now()}`,
        at: Date.now(),
        method: route.method,
        path: concretePath,
        routeName: route.name,
        request: { params, query, headers, body: req.body },
        result: res,
        draft,
      });
      invalidate.runsChanged(qc, pid);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setSendError(errorMessage(e));
    } finally {
      setSending(false);
    }
  };

  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key === "Enter" &&
        !document.querySelector('[role="dialog"]')
      ) {
        e.preventDefault();
        sendRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const res = result?.res;
  const ok2xx = !!res && res.status >= 200 && res.status < 300;
  const canUseSchema =
    !!res && ok2xx && !result!.draft && !dirty && !!result!.route.routeId;
  const schemaTip = !res
    ? ""
    : result!.draft || dirty
      ? "Save the module first: schemas apply to saved routes"
      : !ok2xx
        ? "Only a 2xx response can become the schema"
        : "Store the inferred request/response schema on the route";

  const applySchema = async () => {
    if (!result?.route.routeId) return;
    setApplying(true);
    try {
      const r = await routesApi.test(pid, result.route.routeId, {
        params: req.params,
        query: kvToObj(req.query),
        headers: kvToObj(req.headers),
        body: req.body.trim() ? JSON.parse(req.body) : null,
        environment: env,
        apply_schemas: true,
      });
      await invalidate.routesChanged(qc, pid);
      if (r.applied)
        toast.success("Schema applied", {
          description: `${result.route.method} ${result.route.path}${r.spec_revision ? ` · spec rev ${r.spec_revision}` : ""}`,
        });
      else
        toast.message("Schema not applied", {
          description: `The response was ${r.status}`,
        });
    } catch (e) {
      toast.error("Could not apply the schema", {
        description: errorMessage(e),
      });
    } finally {
      setApplying(false);
    }
  };

  const tableMissing =
    res?.error?.code === "table_not_found"
      ? (res.error.table ?? virtualTables[0] ?? null)
      : null;

  if (routes.length === 0)
    return (
      <div
        className="p-5 text-[13px] text-muted-foreground"
        data-testid="test-no-routes"
      >
        No routes in this module yet. Add a decorator like{" "}
        <code>@api.get("/hello")</code>.
      </div>
    );

  const resultKey = result
    ? `${result.res.execution_id ?? ""}:${result.res.duration_ms}:${recentTests[0]?.id ?? ""}`
    : null;

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="test-panel">
      <div
        className="flex min-h-0 flex-1 flex-col overflow-y-auto pb-3"
        data-scroll-x="allowed"
      >
        <div className="flex flex-col gap-3 px-5 pt-1">
          {!sandboxAvailable && (
            <Callout
              tone="error"
              icon={<AlertTriangle />}
              data-testid="sandbox-unavailable"
            >
              Python can't run on this server (bubblewrap unavailable). You can
              still edit and save; routes return 503.
            </Callout>
          )}
          <div className="flex items-center gap-1.5">
            <div className="min-w-0 flex-1">
              <RoutePicker
                routes={routes.map((r) => ({
                  key: r.name,
                  method: r.method,
                  path: r.path,
                  module: moduleName === "~new" ? undefined : moduleName,
                  testId: `test-route-option-${r.name}`,
                  sub: (
                    <span className="inline-flex items-center gap-1.5">
                      <span className="font-mono">{r.name}</span>
                      {r.draft && <Badge variant="ai">draft</Badge>}
                    </span>
                  ),
                }))}
                value={route?.name ?? null}
                onSelect={onSelect}
                triggerTestId="test-route-picker"
                currentTestId="test-route-current"
                extra={
                  route?.draft ? <Badge variant="ai">draft</Badge> : undefined
                }
              />
            </div>
            {route?.routeId && !route.draft && (
              <RouteSettingsButton routeId={route.routeId} />
            )}
          </div>
          <div className={cn("flex items-center gap-2", narrow && "flex-wrap")}>
            <Segmented<ReqTab>
              value={reqTab}
              onChange={setReqTab}
              aria-label="Request part"
              options={[
                { key: "params", label: "Params", testId: "req-tab-params" },
                { key: "query", label: "Query", testId: "req-tab-query" },
                { key: "body", label: "Body", testId: "req-tab-body" },
                { key: "headers", label: "Headers", testId: "req-tab-headers" },
              ]}
            />
            <div className="flex-1" />
            <Tip label={`Run with ${env} secrets`} kbd="⌘↵">
              <Button
                size="sm"
                onClick={send}
                disabled={!sandboxAvailable || sending}
                data-testid="test-send"
                data-sending={sending || undefined}
              >
                {sending ? <Spinner className="size-3" /> : <Send />}{" "}
                Send <Kbd>⌘↵</Kbd>
              </Button>
            </Tip>
          </div>

          {reqTab === "params" && (
            <div className="flex flex-col gap-1.5" data-testid="req-params">
              {pathParams(route?.path ?? "").length === 0 && (
                <div className="text-xs text-muted-foreground">
                  This route has no path parameters.
                </div>
              )}
              {pathParams(route?.path ?? "").map((p) => (
                <div key={p} className="grid grid-cols-[110px_1fr] gap-1.5">
                  <Input value={p} readOnly className="font-mono text-xs" />
                  <Input
                    value={req.params[p] ?? ""}
                    onChange={(e) =>
                      setReq({ params: { ...req.params, [p]: e.target.value } })
                    }
                    className="font-mono text-xs"
                    data-testid={`param-${p}`}
                  />
                </div>
              ))}
            </div>
          )}
          {(reqTab === "query" || reqTab === "headers") && (
            <KvEditor
              rows={reqTab === "query" ? req.query : req.headers}
              onChange={(rows) =>
                setReq(reqTab === "query" ? { query: rows } : { headers: rows })
              }
              testId={`req-${reqTab}`}
            />
          )}
          {reqTab === "body" && (
            <Textarea
              value={req.body}
              onChange={(e) => setReq({ body: e.target.value })}
              rows={5}
              spellCheck={false}
              placeholder='{"key": "value"}'
              className="resize-y font-mono text-xs"
              data-testid="req-body"
            />
          )}
          <p
            className="text-[12px] leading-[1.5] text-muted-foreground"
            data-testid="test-note"
          >
            {useDraftRun ? (
              <>
                Runs your <b className="font-medium text-foreground">unsaved</b>{" "}
                code with <b className="font-medium text-foreground">{env}</b>{" "}
                secrets. Writes go to your real tables (shared by dev and prod).
                Other modules run as saved.
              </>
            ) : (
              <>
                Runs the saved route with{" "}
                <b className="font-medium text-foreground">{env}</b> secrets.
                Writes go to your real tables (shared by dev and prod).
              </>
            )}
            {missingTablesNote && (
              <>
                {" "}
                <span className="text-foreground">
                  {virtualTables.join(", ")}
                </span>{" "}
                doesn't exist yet: reads return empty.
              </>
            )}
            {authNote && (
              <> Route auth ({route!.auth}) isn't enforced for unsaved code.</>
            )}
          </p>
          {sendError && (
            <div
              className="text-xs text-destructive"
              data-testid="test-send-error"
            >
              {sendError}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-6">
          <div className="px-5 pt-1 -mb-2" data-testid="request-path-art">
            <RequestPath
              route={route}
              trace={res?.trace}
              sandboxMs={res?.trace?.sandbox?.duration_ms ?? null}
              sending={sending}
              resultKey={resultKey}
            />
          </div>

          {res && result && (
            <>
              <div
                className={cn(
                  "flex flex-col gap-3 transition-opacity duration-(--d-fast)",
                  sending && "opacity-50",
                )}
              >
                <div
                  className="mx-5 flex items-center gap-2 whitespace-nowrap"
                  data-testid="test-response"
                >
                  <StatusPill status={res.status} />
                  <span
                    className="inline-flex items-baseline gap-[3px] text-[20px] leading-[1.2] font-semibold tracking-[-0.02em]"
                    data-testid="response-ms"
                  >
                    <Roll value={Math.round(res.duration_ms)} />
                    <small className="text-[12.5px] font-medium tracking-normal text-muted-foreground">
                      ms
                    </small>
                  </span>
                  {res.execution_id && (
                    <Link
                      to="/p/$project/runs/$executionId"
                      params={{ project: slug, executionId: res.execution_id }}
                      className="min-w-0 truncate font-mono text-[12px] text-muted-foreground hover:text-foreground"
                      data-testid="test-execution-link"
                    >
                      · {res.execution_id.slice(0, 9)}…
                      {res.execution_id.slice(-4)}
                    </Link>
                  )}
                  <div className="flex-1" />
                  <Segmented<ResTab>
                    value={resTab}
                    onChange={setResTab}
                    options={[
                      { key: "body", label: "Body", testId: "res-tab-body" },
                      {
                        key: "headers",
                        label: "Headers",
                        testId: "res-tab-headers",
                      },
                      {
                        key: "schema",
                        label: "Schema",
                        testId: "res-tab-schema",
                      },
                    ]}
                  />
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <span tabIndex={canUseSchema ? -1 : 0}>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            disabled={!canUseSchema || applying}
                            onClick={applySchema}
                            data-testid="use-as-schema"
                            aria-label="Use as schema"
                          >
                            <FileJson2 />
                          </Button>
                        </span>
                      }
                    />
                    <TooltipContent>{schemaTip}</TooltipContent>
                  </Tooltip>
                </div>
                {tableMissing && (
                  <div className="mx-5">
                    <CreateTableCard
                      table={tableMissing}
                      columns={draftTables[tableMissing] ?? null}
                      onCreated={() => {
                        invalidate.tablesChanged(qc, pid);
                      }}
                    />
                  </div>
                )}
                {res.error && res.error.code !== "table_not_found" && (
                  <div className="mx-5">
                    <RunErrorBox
                      error={res.error}
                      onGoToLine={
                        !res.error.module ||
                        res.error.module === moduleName ||
                        res.error.module === `${moduleName}.py`
                          ? onGoToLine
                          : undefined
                      }
                    />
                  </div>
                )}
                <div className="mx-2">
                  {resTab === "body" && (
                    <JsonBlock value={res.body} testId="response-body" />
                  )}
                  {resTab === "headers" && (
                    <JsonBlock value={res.headers} testId="response-headers" />
                  )}
                  {resTab === "schema" && (
                    <JsonBlock
                      value={res.inferred?.response_schema ?? null}
                      testId="response-schema"
                    />
                  )}
                </div>
              </div>

              <div
                className={cn(
                  "flex flex-col gap-3 transition-opacity duration-(--d-fast)",
                  sending && "opacity-50",
                )}
                data-testid="test-trace"
              >
                <div className="mx-5 flex items-center gap-1.5 text-[12px]">
                  <Activity className="size-3.5 text-muted-foreground" />
                  <b className="font-semibold">Trace</b>
                  <span className="text-muted-foreground">
                    · {res.trace?.broker_calls?.length ?? 0} node calls ·
                    sandbox {formatMs(res.trace?.sandbox?.duration_ms)}
                  </span>
                </div>
                <Well className="mx-2 px-3 pt-3 pb-2">
                  <TraceView
                    trace={res.trace}
                    replayKey={resultKey ?? undefined}
                    renderFix={(c) => <DeniedFix call={c} />}
                  />
                </Well>
              </div>
            </>
          )}

          {recentTests.length > 0 && (
            <div className="flex flex-col gap-2">
              <div className="mx-5 flex items-center gap-1.5 text-[12px]">
                <History className="size-3.5 text-muted-foreground" />{" "}
                <b className="font-semibold">Recent tests</b>
              </div>
              <div className="mx-3 flex flex-col" data-testid="recent-tests">
                {recentTests.slice(0, 5).map((t) => {
                  const r = routes.find((x) => x.name === t.routeName);
                  return (
                    <button
                      key={t.id}
                      type="button"
                      className="flex h-7 items-center gap-2 rounded-chip px-2 text-left text-[12px] transition-colors duration-(--d-instant) hover:bg-foreground/5"
                      onClick={() => {
                        if (r) onSelect(r.name);
                        const k = r ? `${r.method} ${r.path}` : key;
                        setReqs((s) => ({
                          ...s,
                          [k]: {
                            params: t.request.params,
                            query: objToKv(t.request.query),
                            headers: objToKv(t.request.headers),
                            body: t.request.body,
                          },
                        }));
                        setResult({
                          res: t.result,
                          draft: t.draft,
                          route: r ?? route!,
                        });
                      }}
                      data-testid="recent-test"
                    >
                      <StatusPill status={t.result.status} small />
                      <span className="truncate">
                        {t.method} {t.path}
                      </span>
                      {t.result.error && (
                        <span className="truncate text-muted-foreground">
                          {t.result.error.code ?? t.result.error.message}
                        </span>
                      )}
                      <span className="ml-auto shrink-0 text-muted-foreground tabular-nums">
                        {formatMs(t.result.duration_ms)} ·{" "}
                        {timeAgo(new Date(t.at).toISOString())}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function KvEditor({
  rows,
  onChange,
  testId,
}: {
  rows: KV;
  onChange: (r: KV) => void;
  testId: string;
}) {
  const list =
    rows.length && rows[rows.length - 1].k
      ? [...rows, { k: "", v: "" }]
      : rows.length
        ? rows
        : [{ k: "", v: "" }];
  return (
    <div className="flex flex-col gap-2" data-testid={testId}>
      {list.map((row, i) => (
        <div key={i} className="grid grid-cols-2 gap-2">
          <Input
            value={row.k}
            placeholder="name"
            onChange={(e) =>
              onChange(
                list.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)),
              )
            }
            className="font-mono text-xs"
            data-testid={`${testId}-key-${i}`}
          />
          <Input
            value={row.v}
            placeholder="value"
            onChange={(e) =>
              onChange(
                list.map((x, j) => (j === i ? { ...x, v: e.target.value } : x)),
              )
            }
            className="font-mono text-xs"
            data-testid={`${testId}-value-${i}`}
          />
        </div>
      ))}
    </div>
  );
}

function CreateTableCard({
  table,
  columns,
  onCreated,
}: {
  table: string;
  columns: Column[] | null;
  onCreated: () => void;
}) {
  const { pid } = useProjectCtx();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div
      className="flex items-center gap-3 rounded-ctl bg-warning-soft p-3"
      data-testid="table-not-found"
    >
      <Table2 className="size-4 shrink-0 text-warning-text" />
      <div className="min-w-0 flex-1 text-xs">
        <div className="text-[13px] font-medium">
          Table <code>{table}</code> {done ? "created" : "doesn't exist yet"}
        </div>
        <div className="text-muted-foreground">
          {done
            ? "Send again to test the write."
            : "It's created when you save. Create it now to test writes."}
        </div>
        {error && <div className="mt-1 text-destructive">{error}</div>}
      </div>
      {!done && (
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          data-testid="create-table-now"
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await tablesApi.create(pid, {
                name: table,
                columns: columns ?? [],
              });
              setDone(true);
              onCreated();
            } catch (e) {
              if (isApiError(e, "name_taken")) {
                setDone(true);
                onCreated();
              } else setError(errorMessage(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Create table now
        </Button>
      )}
    </div>
  );
}

/** Inline fixes for denied broker calls (§4.4). */
function DeniedFix({ call }: { call: BrokerCall }) {
  const { project, slug } = useProjectCtx();
  const code = call.error_code;
  const keyFromError = useMemo(
    () => /'([A-Za-z_][A-Za-z0-9_]*)'/.exec(call.error ?? "")?.[1] ?? "",
    [call.error],
  );
  if (code === "missing_secret")
    return (
      <span className="ml-2 inline-block">
        <CreateSecretPopover
          secretKey={keyFromError}
          keyReadOnly={!!keyFromError}
          environments={[project.default_environment]}
          trigger={
            <Button
              variant="outline"
              size="xs"
              data-testid="fix-missing-secret"
            >
              Add {keyFromError || "secret"}
            </Button>
          }
        />
      </span>
    );
  if (code === "secret_not_bound")
    return (
      <span className="ml-1 text-muted-foreground">
        ·{" "}
        <Link
          to="/p/$project/secrets"
          params={{ project: slug }}
          className="text-primary hover:underline"
        >
          Bind {keyFromError || "the secret"} to a host
        </Link>
      </span>
    );
  if (code === "host_not_allowed")
    return (
      <span className="ml-1 text-muted-foreground">
        ·{" "}
        <Link
          to="/p/$project"
          params={{ project: slug }}
          search={{ sheet: "settings", section: "egress" }}
          className="text-primary hover:underline"
        >
          Add host to the project allowlist
        </Link>
      </span>
    );
  if (code === "secret_expression_not_allowed")
    return (
      <span className="ml-1 text-muted-foreground">
        · Don't transform studio.secret(...) (no .upper(), slicing or hashing)
      </span>
    );
  if (code === "read_only")
    return (
      <span className="ml-1 text-muted-foreground">
        · self-tests are read-only
      </span>
    );
  if (code === "call_limit_exceeded")
    return (
      <span className="ml-1 text-muted-foreground">
        · too many node calls in one request
      </span>
    );
  return null;
}

export { jsonText };
