/**
 * Settings · API keys (UI-DESIGN §3.7, §6.5): table (name, environment, prefix, last used, Revoke);
 * New key → name + environment → the key is shown once; Revoke via AlertDialog.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeySquare, Plus } from "lucide-react";
import { useProjectCtx } from "@/features/project/ProjectContext";
import { timeAgo } from "@/features/project/lib/format";
import { apiKeysApi } from "@/shared/api/endpoints";
import { errorMessage } from "@/shared/api/http";
import { invalidate, qk } from "@/shared/api/queryKeys";
import type { ApiKey, ApiKeyCreated } from "@/shared/api/types";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/shared/components/ui/alert-dialog";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { Field } from "@/shared/components/ui/field";
import { Input } from "@/shared/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/shared/components/ui/popover";
import { Segmented } from "@/shared/components/ui/segmented";
import { Well } from "@/shared/components/ui/surface";
import { Skeleton } from "@/shared/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/components/ui/table";
import { InlineError, OnceBox, SectionHeader } from "./common";

export function ApiKeysSection() {
  const { project, pid } = useProjectCtx();
  const qc = useQueryClient();
  const keys = useQuery({
    queryKey: qk.apiKeys(pid),
    queryFn: () => apiKeysApi.list(pid),
  });
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);
  const revoke = useMutation({
    mutationFn: (k: ApiKey) => apiKeysApi.revoke(pid, k.id),
    onSuccess: () => {
      setRevoking(null);
      return invalidate.apiKeysChanged(qc, pid);
    },
    onError: (e) => setRevokeError(errorMessage(e)),
  });
  const rows = [...(keys.data ?? [])].sort(
    (a, b) => Number(!!a.revoked_at) - Number(!!b.revoked_at),
  );

  return (
    <div className="flex flex-col gap-3" data-testid="settings-keys">
      <SectionHeader
        title="API keys"
        sub="server-to-server; never put in apps"
        actions={
          <Popover open={newOpen} onOpenChange={setNewOpen}>
            <PopoverTrigger
              render={
                <Button variant="outline" size="xs" data-testid="apikey-new" />
              }
            >
              <Plus /> New key
            </PopoverTrigger>
            <PopoverContent align="end" className="w-[300px]">
              {newOpen && (
                <NewKeyForm
                  envs={project.environments.map((e) => e.name)}
                  defaultEnv={project.live_environment}
                  onCreated={(k) => {
                    setCreated(k);
                    setNewOpen(false);
                  }}
                />
              )}
            </PopoverContent>
          </Popover>
        }
      />
      {created && (
        <OnceBox
          testId="apikey-once"
          icon={<KeySquare />}
          title={`${created.name} (${created.environment})`}
          value={created.key}
          footer={
            <>
              Send it as{" "}
              <span className="font-mono">Authorization: Bearer …</span> to
              routes with <span className="font-mono">auth="api_key"</span>.{" "}
              <button
                type="button"
                className="underline underline-offset-2 hover:text-foreground"
                onClick={() => setCreated(null)}
              >
                I copied it
              </button>
            </>
          }
        />
      )}
      {keys.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : keys.isError ? (
        <InlineError>
          Couldn't load API keys · {errorMessage(keys.error)}{" "}
          <button
            type="button"
            className="underline"
            onClick={() => keys.refetch()}
          >
            Retry
          </button>
        </InlineError>
      ) : rows.length === 0 ? (
        <Well
          className="px-4 py-5 text-center text-xs text-muted-foreground"
          data-testid="apikeys-empty"
        >
          No API keys. Create one for CI jobs or other servers that call your{" "}
          <span className="font-mono">api_key</span> routes.
        </Well>
      ) : (
        <Well className="px-2 py-1">
          <Table dense className="text-[12.5px]" data-testid="apikeys-table">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Name</TableHead>
                <TableHead>Env</TableHead>
                <TableHead>Prefix</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead className="w-16" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((k) => (
                <TableRow
                  key={k.id}
                  data-testid={`apikey-row-${k.id}`}
                  data-revoked={k.revoked_at ? "true" : undefined}
                >
                  <TableCell
                    className={
                      k.revoked_at
                        ? "text-muted-foreground line-through"
                        : undefined
                    }
                  >
                    {k.name}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{k.environment}</Badge>
                  </TableCell>
                  <TableCell
                    className="font-mono text-muted-foreground"
                    data-testid="apikey-prefix"
                  >
                    {k.prefix}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {k.last_used_at ? timeAgo(k.last_used_at) : "never"}
                  </TableCell>
                  <TableCell className="text-right">
                    {k.revoked_at ? (
                      <Badge variant="neutral">revoked</Badge>
                    ) : (
                      <Button
                        variant="ghost"
                        size="xs"
                        className="text-destructive hover:text-destructive"
                        onClick={() => {
                          setRevokeError(null);
                          setRevoking(k);
                        }}
                        data-testid={`apikey-revoke-${k.id}`}
                      >
                        Revoke
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Well>
      )}
      <AlertDialog
        open={!!revoking}
        onOpenChange={(o) => !o && setRevoking(null)}
      >
        <AlertDialogContent data-testid="apikey-revoke-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke {revoking?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Requests with{" "}
              <span className="font-mono">{revoking?.prefix}…</span> get{" "}
              <span className="font-mono">401</span> immediately. This cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {revokeError && <InlineError>{revokeError}</InlineError>}
          <AlertDialogFooter>
            <AlertDialogCancel size="sm">Cancel</AlertDialogCancel>
            <AlertDialogAction
              size="sm"
              variant="destructive"
              disabled={revoke.isPending}
              onClick={() => revoking && revoke.mutate(revoking)}
              data-testid="apikey-revoke-confirm"
            >
              Revoke key
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function NewKeyForm({
  envs,
  defaultEnv,
  onCreated,
}: {
  envs: string[];
  defaultEnv: string;
  onCreated: (k: ApiKeyCreated) => void;
}) {
  const { pid } = useProjectCtx();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [env, setEnv] = useState(
    envs.includes(defaultEnv) ? defaultEnv : envs[0],
  );
  const create = useMutation({
    mutationFn: () =>
      apiKeysApi.create(pid, { name: name.trim(), environment: env }),
    onSuccess: async (k) => {
      onCreated(k);
      await invalidate.apiKeysChanged(qc, pid);
    },
  });
  return (
    <form
      className="flex flex-col gap-3"
      data-testid="apikey-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) create.mutate();
      }}
    >
      <Field
        label="Name"
        error={create.isError ? errorMessage(create.error) : null}
        errorTestId="apikey-form-error"
      >
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          placeholder="ci-deploy"
          data-testid="apikey-name"
        />
      </Field>
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium">Environment</span>
        <Segmented
          size="xs"
          value={env}
          onChange={setEnv}
          options={envs.map((e) => ({
            key: e,
            label: e,
            testId: `apikey-env-${e}`,
          }))}
        />
      </div>
      <div className="flex justify-end">
        <Button
          type="submit"
          size="sm"
          disabled={!name.trim() || create.isPending}
          data-testid="apikey-create"
        >
          Create key
        </Button>
      </div>
    </form>
  );
}
