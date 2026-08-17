/**
 * Operations reads and writes for the admin console: the audit log, object
 * storage, webhook subscriptions, and team/seat administration.
 *
 * Separate from `useGovernance.ts` on purpose. Governance answers "what is
 * actually protected", and its one hard fact is that only a human-declared
 * column `sensitivity` masks anything. Nothing in THIS file is a control:
 *
 *  - storage retention is a description of how long an artifact is *eligible*
 *    to be kept. There is no scheduler and no cron in the service, so nothing
 *    in it collects anything until a person calls `POST /storage/gc`.
 *    `expired_pending` therefore means "past its deadline and still there",
 *    never "cleaned up";
 *  - a webhook subscription is an annotation. Creating one grants nothing and
 *    deleting one destroys no rows, which is why the delete is wired here at
 *    all (see the rule at the top of `useDatasetActions.ts`);
 *  - a team role grants access inside one team. It is not governance state,
 *    and neither is `masked_columns` on any response — that array is the
 *    masked set FOR THE CALLING SEAT and reads empty for an admin.
 *
 * Every type here is the generated OpenAPI schema rather than a hand-written
 * mirror, so a field that is not in the contract cannot reach the screen.
 *
 * Invalidation is scoped rather than seat-wide. `useDatasetActions` invalidates
 * everything under `['analytics', seat]`, which is right for a dataset write —
 * but the admin console also holds the governance scan, and that costs one
 * request per dataset plus one per sheet. Toggling a webhook must not re-run it.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { analytics, errorText, type Page } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import type { components } from '@/shared/lib/analyticsSchema';

export type AuditEntry = components['schemas']['AuditEntry'];
export type StorageUsage = components['schemas']['StorageUsageResponse'];
export type RetentionPolicy = components['schemas']['RetentionPolicyResponse'];
export type RetentionRule = components['schemas']['RetentionRule'];
export type GcResult = components['schemas']['GcResponse'];
export type WebhookOut = components['schemas']['WebhookOut'];
export type WebhookCreated = components['schemas']['WebhookCreated'];
export type WebhookCreate = components['schemas']['WebhookCreate'];
export type WebhookUpdate = components['schemas']['WebhookUpdate'];
export type DeliveryOut = components['schemas']['DeliveryOut'];
export type UserOut = components['schemas']['UserOut'];
export type CreateUserRequest = components['schemas']['CreateUserRequest'];
export type TeamOut = components['schemas']['TeamOut'];
export type MembershipOut = components['schemas']['MembershipOut'];
export type MemberOut = components['schemas']['MemberOut'];
export type AddMemberRequest = components['schemas']['AddMemberRequest'];
export type TeamRole = components['schemas']['Role'];

/** The roles `PATCH /teams/{id}/members/{id}` and `POST .../members` accept. */
export const TEAM_ROLES: readonly TeamRole[] = ['owner', 'admin', 'editor', 'viewer'];

/**
 * The event types a subscription may filter on, verbatim from the `events`
 * field description on `WebhookCreate`. An empty list means "all", and a value
 * outside this set is persisted and can then never fire — the service does not
 * re-validate a filter after it is stored, so the picker only offers these.
 */
export const WEBHOOK_EVENTS = [
  'validation.passed',
  'validation.failed',
  'tag.promoted',
  'tag.rolled_back',
  'version.ready',
  'transformation.completed',
  'dataset.published',
] as const;

/** The API caps every offset page at 200. */
export const OPS_PAGE_LIMIT = 50;

function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/** Invalidate only the named scopes for this seat. See the header note. */
function useScopedInvalidate() {
  const qc = useQueryClient();
  const seat = useSeat();
  return (...scopes: string[]) => {
    for (const scope of scopes) {
      void qc.invalidateQueries({ queryKey: ['analytics', seat, scope] });
    }
  };
}

/**
 * Shared write wiring: refetch the affected scopes, report the typed message on
 * failure. A toast is never the evidence — the table below it re-reads.
 */
function useOpsMutation<TData, TVars>(
  fn: (vars: TVars) => Promise<TData>,
  opts: { scopes: string[]; success?: (data: TData, vars: TVars) => string },
) {
  const invalidate = useScopedInvalidate();
  return useMutation<TData, unknown, TVars>({
    mutationFn: fn,
    onSuccess: (data, vars) => {
      invalidate(...opts.scopes);
      if (opts.success) toast.success(opts.success(data, vars));
    },
    onError: (e) => toast.error(errorText(e)),
  });
}

/* ------------------------------------------------------------------- audit */

/**
 * The audit log, newest first as the service returns it.
 *
 * `placeholderData` holds the previous page while the next one loads so paging
 * does not blank the table. Nothing here is derived: a row's `status_code` is
 * what the service answered, and a cross-tenant read is recorded as the 404 it
 * was — the log draws no distinction between "absent" and "not yours" because
 * the API does not either.
 */
export function useAuditLog(offset: number, limit: number = OPS_PAGE_LIMIT) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'audit', offset, limit],
    queryFn: () => analytics.get<Page<AuditEntry>>('/audit', { limit, offset }),
    placeholderData: (prev) => prev,
    retry: false,
  });
}

/* ----------------------------------------------------------------- storage */

export function useStorageUsage() {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'storage', 'usage'],
    queryFn: () => analytics.get<StorageUsage>('/storage/usage'),
    retry: false,
  });
}

/**
 * The retention policy plus what is currently due.
 *
 * `retention_days: null` means kept indefinitely. `expired_pending` counts
 * artifacts already past their deadline and STILL PRESENT — the service has no
 * scheduler, so nothing removes them until someone runs the sweep below.
 */
export function useRetentionPolicy() {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'storage', 'retention'],
    queryFn: () => analytics.get<RetentionPolicy>('/storage/retention'),
    retry: false,
  });
}

/**
 * One garbage-collection sweep, run now because a person asked for it.
 *
 * This is the only thing in the system that deletes an expired artifact. It is
 * bounded per call: `more_remaining` true means the sweep stopped at its limit
 * with collectable items left, and false means it reached the end of the
 * backlog — which is the only case where zero deletions really does mean
 * nothing was due.
 */
export function useRunStorageGc() {
  return useOpsMutation<GcResult, void>(() => analytics.post<GcResult>('/storage/gc'), {
    scopes: ['storage'],
    success: (d) =>
      d.expired_deleted + d.orphans_deleted === 0
        ? 'Sweep finished. Nothing was collected.'
        : `Swept ${(d.expired_deleted + d.orphans_deleted).toLocaleString()} artifacts.`,
  });
}

/* ---------------------------------------------------------------- webhooks */

export function useWebhooks(limit: number = OPS_PAGE_LIMIT) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'webhooks', 'list', limit],
    queryFn: () => analytics.get<Page<WebhookOut>>('/webhooks', { limit, offset: 0 }),
    retry: false,
  });
}

/**
 * One subscription, re-read from the server.
 *
 * The list already carries every field this returns, but the detail read is
 * what confirms a PATCH landed — the signing secret is never in either, so
 * there is nothing here that only creation can show.
 */
export function useWebhook(subscriptionId: string | null) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'webhooks', 'detail', subscriptionId],
    queryFn: () => analytics.get<WebhookOut>(`/webhooks/${subscriptionId}`),
    enabled: Boolean(subscriptionId),
    retry: false,
  });
}

export function useWebhookDeliveries(subscriptionId: string | null, limit: number = OPS_PAGE_LIMIT) {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'webhooks', 'deliveries', subscriptionId, limit],
    queryFn: () =>
      analytics.get<Page<DeliveryOut>>(`/webhooks/${subscriptionId}/deliveries`, {
        limit,
        offset: 0,
      }),
    enabled: Boolean(subscriptionId),
    retry: false,
  });
}

/**
 * Create a subscription.
 *
 * The response is the ONLY time the signing secret is ever returned, so the
 * caller must render it rather than discard it. `team_id` is left off the query
 * deliberately: the seat's `X-Team-Id` header already names the team, and
 * sending both is two sources of truth for one scope.
 */
export function useCreateWebhook() {
  return useOpsMutation<WebhookCreated, WebhookCreate>(
    (body) => analytics.post<WebhookCreated>('/webhooks', body),
    { scopes: ['webhooks'], success: (d) => `Subscription "${d.name}" created.` },
  );
}

/**
 * Partial update — an omitted field is unchanged. Enable/disable is this
 * endpoint with `{ enabled }`; there is no separate route.
 */
export function useUpdateWebhook() {
  return useOpsMutation<WebhookOut, { id: string; patch: WebhookUpdate }>(
    ({ id, patch }) => analytics.patch<WebhookOut>(`/webhooks/${id}`, patch),
    { scopes: ['webhooks'], success: () => 'Subscription updated.' },
  );
}

/**
 * Remove a subscription. Wired because it destroys no rows: a subscription is
 * an annotation, and re-creating one restores the behaviour (with a new
 * secret). Past deliveries go with it, so the UI still confirms first.
 */
export function useDeleteWebhook() {
  return useOpsMutation<unknown, string>((id) => analytics.del(`/webhooks/${id}`), {
    scopes: ['webhooks'],
    success: () => 'Subscription removed.',
  });
}

/**
 * Send a real delivery to the subscription's URL. Not a dry run: the endpoint
 * returns a `DeliveryOut` because an actual attempt was recorded.
 */
export function useTestWebhook() {
  return useOpsMutation<DeliveryOut, string>(
    (id) => analytics.post<DeliveryOut>(`/webhooks/${id}/test`),
    {
      scopes: ['webhooks'],
      success: (d) =>
        d.response_status != null
          ? `Delivery attempted — endpoint answered ${d.response_status}.`
          : `Delivery ${d.status}.`,
    },
  );
}

/* ------------------------------------------------------------ teams, seats */

/**
 * The teams this seat belongs to, with its role in each.
 *
 * This is a membership list, not a directory of the platform's teams. A team
 * the seat does not belong to is absent here and answers 404 everywhere else —
 * never 403.
 */
export function useMyTeams() {
  const seat = useSeat();
  return useQuery({
    queryKey: ['analytics', seat, 'teams'],
    queryFn: () => analytics.get<Page<MembershipOut>>('/teams'),
    retry: false,
  });
}

export function useCreateTeam() {
  return useOpsMutation<TeamOut, { name: string }>(
    (body) => analytics.post<TeamOut>('/teams', body),
    { scopes: ['teams', 'auth-me'], success: (d) => `Team "${d.name}" created.` },
  );
}

/**
 * Create a seat.
 *
 * `team_id` defaults to the caller's active team. `is_superuser` is not a team
 * role — it lives on the user record, bypasses team scope entirely, and
 * resolves raw values in every team, so it is never granted implicitly here.
 */
export function useCreateUser() {
  return useOpsMutation<UserOut, CreateUserRequest>(
    (body) => analytics.post<UserOut>('/auth/users', body),
    { scopes: ['members', 'teams'], success: (d) => `Seat created for ${d.email}.` },
  );
}

/**
 * Add someone to a team by internal id *or* exact email.
 *
 * Email exists because there is no user-directory endpoint and a 409 from
 * `POST /auth/users` carries no id, so an id-only contract would make "add a
 * colleague" unbuildable. A miss returns the same 404 as an unknown id.
 */
export function useAddMember(teamId: string | null) {
  return useOpsMutation<MemberOut, AddMemberRequest>(
    (body) => analytics.post<MemberOut>(`/teams/${teamId}/members`, body),
    { scopes: ['members', 'teams'], success: (d) => `${d.name} added as ${d.role}.` },
  );
}

export function useUpdateMemberRole(teamId: string | null) {
  return useOpsMutation<MemberOut, { userId: string; role: TeamRole }>(
    ({ userId, role }) => analytics.patch<MemberOut>(`/teams/${teamId}/members/${userId}`, { role }),
    { scopes: ['members', 'teams', 'auth-me'], success: (d) => `${d.name} is now ${d.role}.` },
  );
}

/**
 * Remove a membership. This revokes access to one team; it deletes no datasets,
 * no versions and no rows, and the user record itself is untouched.
 */
export function useRemoveMember(teamId: string | null) {
  return useOpsMutation<unknown, string>(
    (userId) => analytics.del(`/teams/${teamId}/members/${userId}`),
    { scopes: ['members', 'teams', 'auth-me'], success: () => 'Membership removed.' },
  );
}
