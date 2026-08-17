/**
 * The admin, governance and operations console.
 *
 * This screen exists to answer one question honestly: **what is actually
 * protected?** Everything on it is arranged around the fact that two different
 * things on this platform sound like the same thing and are not:
 *
 *   - column `sensitivity`, in the data dictionary, is the ONLY thing that
 *     masks a value, and every entry is typed in by a person;
 *   - dataset `classification` is a catalog label and enforces nothing.
 *
 * So the two are never drawn alike. Sensitivity gets the counts, the coverage
 * bars, the review status and the lock. Classification gets a flat list of
 * words and a sentence saying it is not a control. Putting a "restricted" badge
 * next to a masking figure would let someone leave this page believing a
 * dataset is protected when no column has been declared — which is the single
 * most damaging thing this console could do.
 *
 * The third region is the review queue, ranked by UNDECLARED columns, because
 * that is where the exposure actually is: nothing is auto-detected, so a column
 * nobody has declared is readable by everyone who can read the dataset.
 *
 * Below the governance regions sit the operational ones — storage, webhooks and
 * the audit log — and they carry a second honesty burden. **There is no
 * scheduler and no cron in this service.** A retention rule is a description of
 * eligibility, not a job: `expired_pending` counts artifacts that are past their
 * deadline and STILL THERE, and the only thing that ever removes one is a person
 * pressing "Run sweep". Drawing retention as though it enforced itself would be
 * the storage-shaped version of the classification mistake above, so the region
 * says what it is at every point where a reader could assume otherwise.
 */

import { useMemo, useState, type ReactNode } from 'react';
import { Lock, ShieldCheck } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableNumericCell,
  TableRow,
} from '@/shared/components/ui/table';
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { Button } from '@/shared/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover';
import JsonViewer from '@/shared/components/ui/json-viewer';
import {
  Eyebrow,
  Figure,
  Footnote,
  Identifier,
  Metric,
} from '@/shared/components/instrument/Typography';
import { Status, type StatusKind } from '@/shared/components/instrument/Status';
import { Severity } from '@/shared/components/instrument/Severity';
import { Guard } from '@/shared/components/instrument/Guard';
import { Stat } from '@/shared/components/instrument/Stat';
import { MagnitudeBar } from '@/shared/components/instrument/charts';
import { coverage } from '@/shared/components/instrument/coverage';
import { compact, formatBytes, formatSizeParts, num, shortDate } from '@/shared/lib/format';
import { errorText } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import { cn } from '@/shared/lib/utils';
import { useAuthMe, useTeamMembers, type TeamMember } from '../hooks/useDatasets';
import {
  MAX_SCANNED_DATASETS,
  ROLE_RANK,
  SENSITIVE_LEVELS,
  roleSeesRaw,
  seatSeesRaw,
  useDatasetFacets,
  useGovernanceScan,
  type DatasetGovernance,
} from '../hooks/useGovernance';
import {
  OPS_PAGE_LIMIT,
  TEAM_ROLES,
  WEBHOOK_EVENTS,
  useAddMember,
  useAuditLog,
  useCreateTeam,
  useCreateUser,
  useCreateWebhook,
  useDeleteWebhook,
  useMyTeams,
  useRemoveMember,
  useRetentionPolicy,
  useRunStorageGc,
  useStorageUsage,
  useTestWebhook,
  useUpdateMemberRole,
  useUpdateWebhook,
  useWebhook,
  useWebhookDeliveries,
  useWebhooks,
  type AddMemberRequest,
  type TeamRole,
  type WebhookOut,
} from '../hooks/useOps';
import { fieldClass } from './fieldStyles';

/**
 * The four labels the write path accepts. Read responses are unconstrained
 * strings, so anything else the facets return is still rendered — it just
 * sorts after these.
 */
const LABEL_ORDER = ['public', 'internal', 'confidential', 'restricted'];

/** The two labels a reader is most likely to mistake for a control. */
const LABELS_THAT_SOUND_PROTECTIVE = new Set(['confidential', 'restricted']);

/** Roles are ordinal — they rank by VALUE, never by hue. */
function roleClass(role: string): string {
  const rank = ROLE_RANK[role.toLowerCase()] ?? 0;
  if (rank >= 40) return 'font-semibold text-foreground';
  if (rank >= 30) return 'text-foreground';
  if (rank >= 20) return 'text-muted-foreground';
  return 'text-muted-foreground/80';
}

/**
 * Whether a role resolves raw values.
 *
 * Typographic, not coloured, and for the same reason severity is: this is a
 * *property* of the role, true before anyone looks at anything. Colouring it
 * would have a capability impersonating live status.
 */
function RawAccess({ role }: { role: string }) {
  const raw = roleSeesRaw(role);
  const editor = role.toLowerCase() === 'editor';

  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={raw ? 'font-semibold text-foreground' : 'text-muted-foreground'}>
        {raw ? 'raw' : 'masked'}
      </span>
      {editor && <Footnote className="italic">withheld by policy</Footnote>}
    </span>
  );
}

/**
 * An HTTP status code from a log row.
 *
 * Typographic, like `Severity` and for the same reason: this is what the service
 * answered some time ago, not a live state, and spending the reserved status
 * palette on a table of past answers would leave a page of routine 404s — the
 * deliberate cross-tenant answer — reading as a page on fire.
 */
function HttpStatus({ code }: { code: number | null | undefined }) {
  if (code == null) return <span className="text-muted-foreground">—</span>;
  return (
    <span className={code >= 400 ? 'font-semibold text-foreground' : 'text-muted-foreground'}>
      {code}
    </span>
  );
}

/**
 * A delivery's state. Unrecognised values fall to `unknown` rather than being
 * guessed at — `status` is a plain string in the contract, and a state this
 * screen cannot name is exactly what the ring shape is for.
 */
function deliveryStatusKind(status: string): StatusKind {
  const s = status.trim().toLowerCase();
  if (s === 'delivered' || s === 'success' || s === 'succeeded') return 'good';
  if (s === 'failed' || s === 'failure') return 'critical';
  if (s === 'retrying' || s === 'retry') return 'warning';
  return 'unknown';
}

/**
 * Wall-clock time from a timestamp. `shortDate` gives the day; an audit row and
 * a delivery attempt both need the second as well. Guarded the same way: most
 * entities emit a Postgres `::text` timestamp, and a malformed one renders as an
 * em dash rather than "Invalid Date".
 */
function clockTime(value: string | null | undefined): string {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toISOString().slice(11, 19);
}

/** A label-over-value pair for the detail popovers. */
function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <Eyebrow>{label}</Eyebrow>
      <div className="text-small break-all text-foreground">{children}</div>
    </div>
  );
}

export function AdminPage() {
  const me = useAuthMe();
  const seatTeamId = useIdentityStore((s) => s.identity.teamId);

  const memberships = useMemo(() => me.data?.memberships ?? [], [me.data]);
  // The seat may not name a team; the service then defaults to the user's own,
  // which is the first membership it reports back.
  const team = useMemo(
    () => memberships.find((m) => m.team_id === seatTeamId) ?? memberships[0] ?? null,
    [memberships, seatTeamId],
  );

  const members = useTeamMembers(team?.team_id ?? null);
  const facets = useDatasetFacets();
  const scan = useGovernanceScan();

  const teams = useMyTeams();
  const createTeam = useCreateTeam();
  const createUser = useCreateUser();
  const addMember = useAddMember(team?.team_id ?? null);
  const updateMemberRole = useUpdateMemberRole(team?.team_id ?? null);
  const removeMember = useRemoveMember(team?.team_id ?? null);

  const usage = useStorageUsage();
  const retention = useRetentionPolicy();
  const gc = useRunStorageGc();

  const [selectedHook, setSelectedHook] = useState<string | null>(null);
  const hooks = useWebhooks();
  const hookDetail = useWebhook(selectedHook);
  const deliveries = useWebhookDeliveries(selectedHook);
  const createWebhook = useCreateWebhook();
  const updateWebhook = useUpdateWebhook();
  const deleteWebhook = useDeleteWebhook();
  const testWebhook = useTestWebhook();

  const [auditOffset, setAuditOffset] = useState(0);
  const audit = useAuditLog(auditOffset);

  const [teamName, setTeamName] = useState('');
  const [memberRef, setMemberRef] = useState('');
  const [memberRole, setMemberRole] = useState<TeamRole>('viewer');
  const [seatEmail, setSeatEmail] = useState('');
  const [seatName, setSeatName] = useState('');
  const [seatSuperuser, setSeatSuperuser] = useState(false);
  const [hookName, setHookName] = useState('');
  const [hookUrl, setHookUrl] = useState('');
  const [hookEvents, setHookEvents] = useState<string[]>([]);
  const [hookEnabled, setHookEnabled] = useState(true);

  const [hookToDelete, setHookToDelete] = useState<WebhookOut | null>(null);
  const [memberToRemove, setMemberToRemove] = useState<TeamMember | null>(null);

  const seats = useMemo(() => {
    const items = members.data?.items ?? [];
    return [...items].sort(
      (a, b) =>
        (ROLE_RANK[b.role?.toLowerCase() ?? ''] ?? 0) -
          (ROLE_RANK[a.role?.toLowerCase() ?? ''] ?? 0) ||
        a.name.localeCompare(b.name),
    );
  }, [members.data]);

  const totals = useMemo(() => {
    const rows = scan.data?.datasets ?? [];
    return rows.reduce(
      (acc, d) => ({
        columns: acc.columns + d.columns,
        masked: acc.masked + d.masked,
        declaredOpen: acc.declaredOpen + d.declaredOpen,
        undeclared: acc.undeclared + d.undeclared,
        unreadable: acc.unreadable + (d.readable ? 0 : 1),
      }),
      { columns: 0, masked: 0, declaredOpen: 0, undeclared: 0, unreadable: 0 },
    );
  }, [scan.data]);

  /**
   * Datasets wearing a label that sounds protective while declaring nothing.
   * This is the whole argument of the screen, computed rather than asserted.
   */
  const labelOnly = useMemo(
    () =>
      (scan.data?.datasets ?? []).filter(
        (d) =>
          d.readable && d.masked === 0 && LABELS_THAT_SOUND_PROTECTIVE.has(d.classification),
      ),
    [scan.data],
  );

  const classifications = useMemo(() => {
    const map = facets.data?.classification ?? {};
    return Object.entries(map).sort(([a], [b]) => {
      const ia = LABEL_ORDER.indexOf(a);
      const ib = LABEL_ORDER.indexOf(b);
      if (ia !== ib) return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      return a.localeCompare(b);
    });
  }, [facets.data]);

  const inertLevels = useMemo(
    () => Object.entries(scan.data?.inertLevels ?? {}).sort((a, b) => b[1] - a[1]),
    [scan.data],
  );

  const totalBytes = usage.data?.total_bytes ?? 0;
  const totalParts = formatSizeParts(usage.data ? totalBytes : null);
  const storageParts = useMemo<[string, number][]>(() => {
    const u = usage.data;
    if (!u) return [];
    return [
      ['datasets', u.datasets_bytes],
      ['samples', u.samples_bytes],
      ['exports', u.exports_bytes],
      ['uploads', u.uploads_bytes],
    ];
  }, [usage.data]);
  const gcByType = useMemo(() => Object.entries(gc.data?.by_type ?? {}), [gc.data]);

  const auditItems = audit.data?.items ?? [];
  const auditTotal = audit.data?.total ?? 0;
  const auditFirst = auditItems.length === 0 ? 0 : auditOffset + 1;
  const auditLast = auditOffset + auditItems.length;

  const selectedWebhook = hookDetail.data ?? null;

  const toggleEvent = (event: string) =>
    setHookEvents((prev) =>
      prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event],
    );

  const submitTeam = () => {
    const name = teamName.trim();
    if (!name) return;
    createTeam.mutate({ name }, { onSuccess: () => setTeamName('') });
  };

  const submitMember = () => {
    const ref = memberRef.trim();
    if (!ref || !team) return;
    // The contract takes an internal id *or* an exact address, and there is no
    // directory to look one up in. Routing on `@` is stated in the note below
    // rather than guessed at silently.
    const body: AddMemberRequest = ref.includes('@')
      ? { email: ref, role: memberRole }
      : { user_id: ref, role: memberRole };
    addMember.mutate(body, { onSuccess: () => setMemberRef('') });
  };

  const submitSeat = () => {
    const email = seatEmail.trim();
    const name = seatName.trim();
    if (!email || !name) return;
    // `team_id` is omitted deliberately: the service defaults it to the caller's
    // active team, which is the team named in the scope lamp above.
    createUser.mutate(
      { email, name, is_superuser: seatSuperuser },
      {
        onSuccess: () => {
          setSeatEmail('');
          setSeatName('');
          setSeatSuperuser(false);
        },
      },
    );
  };

  const submitWebhook = () => {
    const name = hookName.trim();
    const url = hookUrl.trim();
    if (!name || !url) return;
    createWebhook.mutate(
      { name, url, events: hookEvents, enabled: hookEnabled },
      {
        onSuccess: () => {
          setHookName('');
          setHookUrl('');
          setHookEvents([]);
          setHookEnabled(true);
        },
      },
    );
  };

  const reviewRow = (d: DatasetGovernance) => {
    const declared = d.masked + d.declaredOpen;
    return (
      <TableRow key={d.id} data-testid="review-row">
        <TableCell className="max-w-[220px] truncate font-medium text-foreground">
          {d.name}
        </TableCell>
        <TableNumericCell className="text-muted-foreground">
          {d.readable ? num(d.columns) : '—'}
        </TableNumericCell>
        <TableNumericCell data-testid="review-masked">
          {d.readable ? (
            <span className={d.masked > 0 ? 'font-semibold text-foreground' : 'text-muted-foreground'}>
              {num(d.masked)}
            </span>
          ) : (
            '—'
          )}
        </TableNumericCell>
        <TableNumericCell className="text-muted-foreground">
          {d.readable ? num(d.declaredOpen) : '—'}
        </TableNumericCell>
        <TableNumericCell data-testid="review-undeclared">
          <span
            className={d.undeclared > 0 ? 'font-semibold text-foreground' : 'text-muted-foreground'}
          >
            {d.readable ? num(d.undeclared) : '—'}
          </span>
        </TableNumericCell>
        <TableCell className="w-[110px]">
          {d.readable && d.columns > 0 ? (
            <MagnitudeBar of={coverage(declared, d.columns, 'columns')} className="h-1" />
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell>
          {!d.readable ? (
            <Status kind="unknown">no answer</Status>
          ) : d.undeclared === 0 ? (
            <Status kind="good">reviewed</Status>
          ) : (
            <Status kind="warning">unreviewed</Status>
          )}
        </TableCell>
      </TableRow>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="admin-page">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-6xl flex-col gap-5 p-5">
          {/* ------------------------------------------------ heading + scope */}
          <div className="flex items-start gap-4">
            <div className="min-w-0 flex-1">
              <h1 className="flex items-center gap-2 text-figure font-medium">
                <ShieldCheck className="size-4 text-muted-foreground" />
                Admin and governance
              </h1>
              <p className="mt-0.5 text-body text-muted-foreground">
                Who holds a seat, what has been declared sensitive, and what nobody has looked
                at yet.
              </p>
            </div>

            {/* The screen's ONE accent use. Rule 1: the accent means scope —
             * "what am I looking at" — and everything below is scoped to this
             * team. Nothing else here may take it. */}
            <div
              className="flex shrink-0 items-center gap-2 rounded-md bg-secondary px-2.5 py-1.5 shadow-[var(--hi)]"
              data-testid="admin-scope"
            >
              <span
                aria-hidden="true"
                className="size-[5px] shrink-0 rounded-full bg-[var(--sig)] shadow-[0_0_8px_-1px_var(--sig)]"
              />
              <div className="min-w-0">
                <Eyebrow>Team scope</Eyebrow>
                <div className="text-body font-medium text-foreground">
                  {team?.team_name ?? '—'}
                </div>
              </div>
              <div className="ml-2 min-w-0">
                <Eyebrow>Acting as</Eyebrow>
                <div className="text-body text-foreground">
                  {me.data?.user.name ?? '—'}
                  <span className="ml-1.5 text-muted-foreground">
                    {team?.role ?? 'no role'}
                    {me.data?.user.is_superuser ? ' · superuser' : ''}
                    {me.data
                      ? seatSeesRaw(team?.role, me.data.user.is_superuser)
                        ? ' · sees raw'
                        : ' · sees masked'
                      : ''}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* -------------------------------------------------- figure strip */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4" data-testid="admin-metrics">
            <Metric
              label="Seats"
              value={num(seats.length)}
              note={team ? `on ${team.team_name}` : 'no team resolved for this seat'}
              data-testid="metric-seats"
            />
            <Metric
              label="Columns masked"
              value={compact(totals.masked)}
              note="declared sensitive — hidden from viewer and editor"
              data-testid="metric-masked"
            />
            <Metric
              label="Columns undeclared"
              value={compact(totals.undeclared)}
              note="never masked — nobody has declared them"
              data-testid="metric-undeclared"
            />
            <Metric
              label="Datasets scanned"
              value={num(scan.data?.scanned ?? 0)}
              note={
                scan.data
                  ? `of ${num(scan.data.total)} in this seat's catalog`
                  : scan.isLoading
                    ? 'scanning…'
                    : '—'
              }
              data-testid="metric-scanned"
            />
          </div>

          {/* ================================================= 1 · TEAMS */}
          <Card data-testid="teams-card">
            <CardHeader>
              <CardTitle>Teams</CardTitle>
              <Footnote className="ml-auto">your memberships</Footnote>
            </CardHeader>
            <CardContent>
              <p className="mb-2 text-body text-muted-foreground">
                Every dataset, artifact and subscription on this page is scoped to one team. This
                is the list of teams <span className="text-foreground">this seat belongs to</span>{' '}
                — not a directory of the platform. A team you are not a member of is absent here
                and answers <span className="text-foreground">404</span> everywhere else, never
                403.
              </p>

              {teams.isError ? (
                <p className="py-4 text-center text-body text-muted-foreground">
                  {errorText(teams.error, { notFound: 'No teams visible from this seat.' })}
                </p>
              ) : (
                <div className="overflow-hidden rounded-md">
                  <Table containerClassName="max-h-[220px]" data-testid="teams-table">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Team</TableHead>
                        <TableHead>Your role</TableHead>
                        <TableHead>Sees values as</TableHead>
                        <TableHead>team_id</TableHead>
                        <TableHead>Scope</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(teams.data?.items ?? []).map((m) => (
                        <TableRow key={m.team_id} data-testid="team-row">
                          <TableCell className="font-medium text-foreground">
                            {m.team_name}
                          </TableCell>
                          <TableCell>
                            <span className={cn('text-body', roleClass(m.role))}>{m.role}</span>
                          </TableCell>
                          <TableCell>
                            <RawAccess role={m.role} />
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            <Identifier className="text-small">{m.team_id}</Identifier>
                          </TableCell>
                          <TableCell>
                            {m.team_id === team?.team_id ? (
                              <Status kind="good">in scope</Status>
                            ) : (
                              <Footnote>not in scope</Footnote>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {!teams.isLoading && (teams.data?.items.length ?? 0) === 0 && (
                    <p className="py-4 text-center text-body text-muted-foreground">
                      This seat belongs to no team.
                    </p>
                  )}
                </div>
              )}

              <div className="mt-3 flex flex-wrap items-end gap-2">
                <div className="min-w-[220px]">
                  <label className="block text-micro text-muted-foreground" htmlFor="new-team-name">
                    New team name
                  </label>
                  <input
                    id="new-team-name"
                    value={teamName}
                    onChange={(e) => setTeamName(e.target.value)}
                    aria-label="New team name"
                    className={fieldClass}
                    data-testid="new-team-name"
                  />
                </div>
                <Button
                  size="xs"
                  onClick={submitTeam}
                  disabled={createTeam.isPending || teamName.trim() === ''}
                  data-testid="create-team"
                >
                  {createTeam.isPending ? 'Creating…' : 'Create team'}
                </Button>
              </div>

              {createTeam.data && (
                <Guard className="mt-2" data-testid="created-team">
                  <span className="font-semibold text-foreground">
                    Created “{createTeam.data.name}”
                  </span>{' '}
                  — <Identifier className="text-foreground">{createTeam.data.id}</Identifier>,{' '}
                  {shortDate(createTeam.data.created_at)}. Switching the seat's scope to it is done
                  through the identity switcher, not from here.
                </Guard>
              )}
            </CardContent>
          </Card>

          {/* ================================================= 2 · SEATS */}
          <Card data-testid="seats-card">
            <CardHeader>
              <CardTitle>Seats and roles</CardTitle>
              <Footnote className="ml-auto">
                {team ? <Identifier>{team.team_id}</Identifier> : 'no team'}
              </Footnote>
            </CardHeader>
            <CardContent>
              <p className="mb-2 text-body text-muted-foreground">
                A role grants nothing outside this team. Masking hides values from a{' '}
                <span className="text-foreground">viewer</span> and an{' '}
                <span className="text-foreground">editor</span> alike — only{' '}
                <span className="text-foreground">admin</span>,{' '}
                <span className="text-foreground">owner</span> and a platform{' '}
                <span className="text-foreground">superuser</span> resolve raw values.
              </p>

              {members.isError ? (
                <p className="py-4 text-center text-body text-muted-foreground">
                  {errorText(members.error, {
                    notFound: 'No members visible for this team from this seat.',
                  })}
                </p>
              ) : (
                <div className="overflow-hidden rounded-md">
                  <Table containerClassName="max-h-[280px]" data-testid="seats-table">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Seat</TableHead>
                        <TableHead>Email</TableHead>
                        <TableHead>Role</TableHead>
                        <TableHead>Sees values as</TableHead>
                        <TableHead>Manage</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {seats.map((m) => (
                        <TableRow key={m.user_id} data-testid="seat-row">
                          <TableCell className="font-medium text-foreground">{m.name}</TableCell>
                          <TableCell className="text-muted-foreground">
                            <Identifier className="text-small">{m.email}</Identifier>
                          </TableCell>
                          <TableCell>
                            <span className={cn('text-body', roleClass(m.role))}>{m.role}</span>
                          </TableCell>
                          <TableCell>
                            <RawAccess role={m.role} />
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-2">
                              <select
                                value={m.role}
                                aria-label={`Change role for ${m.name}`}
                                className={cn(fieldClass, 'w-[84px]')}
                                disabled={!team || updateMemberRole.isPending}
                                onChange={(e) =>
                                  updateMemberRole.mutate({
                                    userId: m.user_id,
                                    role: e.target.value as TeamRole,
                                  })
                                }
                                data-testid="member-role"
                              >
                                {TEAM_ROLES.map((r) => (
                                  <option key={r} value={r}>
                                    {r}
                                  </option>
                                ))}
                              </select>
                              <Button
                                variant="ghost"
                                size="xs"
                                onClick={() => setMemberToRemove(m)}
                                disabled={!team || removeMember.isPending}
                                data-testid="remove-member"
                              >
                                Remove
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {!members.isLoading && seats.length === 0 && (
                    <p className="py-4 text-center text-body text-muted-foreground">
                      No seats to show.
                    </p>
                  )}
                </div>
              )}

              {/* Add an existing person to this team. */}
              <div className="mt-3 flex flex-wrap items-end gap-2" data-testid="add-member-form">
                <div className="min-w-[260px] flex-1">
                  <label className="block text-micro text-muted-foreground" htmlFor="member-ref">
                    Add to this team — user id or exact email
                  </label>
                  <input
                    id="member-ref"
                    value={memberRef}
                    onChange={(e) => setMemberRef(e.target.value)}
                    aria-label="User id or email to add"
                    className={fieldClass}
                    data-testid="member-ref"
                  />
                </div>
                <div>
                  <label className="block text-micro text-muted-foreground" htmlFor="member-role-new">
                    Role
                  </label>
                  <select
                    id="member-role-new"
                    value={memberRole}
                    onChange={(e) => setMemberRole(e.target.value as TeamRole)}
                    aria-label="Role for the new member"
                    className={cn(fieldClass, 'w-[84px]')}
                  >
                    {TEAM_ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                </div>
                <Button
                  size="xs"
                  onClick={submitMember}
                  disabled={!team || addMember.isPending || memberRef.trim() === ''}
                  data-testid="add-member"
                >
                  {addMember.isPending ? 'Adding…' : 'Add member'}
                </Button>
              </div>
              <Footnote className="mt-1">
                A value containing <Identifier className="text-foreground">@</Identifier> is sent as{' '}
                <Identifier className="text-foreground">email</Identifier>, anything else as{' '}
                <Identifier className="text-foreground">user_id</Identifier>. There is no
                user-directory endpoint, and a miss answers 404 whichever you send — the same
                answer as "exists, but not yours".
              </Footnote>

              {/* Create a brand new seat. */}
              <div className="mt-3 flex flex-wrap items-end gap-2" data-testid="create-seat-form">
                <div className="min-w-[200px] flex-1">
                  <label className="block text-micro text-muted-foreground" htmlFor="seat-email">
                    New seat — email
                  </label>
                  <input
                    id="seat-email"
                    type="email"
                    value={seatEmail}
                    onChange={(e) => setSeatEmail(e.target.value)}
                    aria-label="New seat email"
                    className={fieldClass}
                    data-testid="seat-email"
                  />
                </div>
                <div className="min-w-[160px] flex-1">
                  <label className="block text-micro text-muted-foreground" htmlFor="seat-name">
                    Name
                  </label>
                  <input
                    id="seat-name"
                    value={seatName}
                    onChange={(e) => setSeatName(e.target.value)}
                    aria-label="New seat name"
                    className={fieldClass}
                    data-testid="seat-name"
                  />
                </div>
                <label className="flex h-6 items-center gap-1.5 text-small text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={seatSuperuser}
                    onChange={(e) => setSeatSuperuser(e.target.checked)}
                    data-testid="seat-superuser"
                  />
                  platform superuser
                </label>
                <Button
                  size="xs"
                  onClick={submitSeat}
                  disabled={
                    createUser.isPending || seatEmail.trim() === '' || seatName.trim() === ''
                  }
                  data-testid="create-seat"
                >
                  {createUser.isPending ? 'Creating…' : 'Create seat'}
                </Button>
              </div>

              {createUser.data && (
                <Guard className="mt-2" data-testid="created-seat">
                  <span className="font-semibold text-foreground">
                    {createUser.data.name} · {createUser.data.email}
                  </span>{' '}
                  — <Identifier className="text-foreground">{createUser.data.id}</Identifier>,
                  status {createUser.data.status ?? 'active'}, created{' '}
                  {shortDate(createUser.data.created_at)}
                  {createUser.data.is_superuser ? ', platform superuser' : ''}. The id is returned
                  once and there is no directory to look it up in again; if it is lost, add the
                  person to a team by exact email instead.
                </Guard>
              )}

              <Card nested className="mt-3" data-testid="read-sensitive-note">
                <Footnote className="leading-relaxed">
                  <span className="font-semibold text-foreground">
                    dataset:read_sensitive is withheld from editor by policy, not by rank.
                  </span>{' '}
                  An editor can upload, transform and publish rows they are never allowed to read
                  unmasked. The platform superuser flag lives on the user record, not on this
                  list, and bypasses team scope entirely — so a seat shown here as{' '}
                  <span className="text-foreground">masked</span> may still be a superuser
                  elsewhere. Another team's members, datasets and artifacts answer{' '}
                  <span className="text-foreground">404</span>, never 403: "not found" and "not
                  yours" are deliberately the same answer.
                </Footnote>
              </Card>

              <Card nested className="mt-2" data-testid="masked-columns-note">
                <Footnote className="leading-relaxed">
                  <span className="font-semibold text-foreground">
                    masked_columns is a per-seat answer, never governance state.
                  </span>{' '}
                  The array a query, sheet or preview response carries is the masked set{' '}
                  <span className="text-foreground">for whoever asked</span>. An empty one means
                  "nothing is declared" OR "you are admin, owner or superuser", and the two are
                  indistinguishable — so reading it as policy would report a governance hole as
                  clean exactly when an admin is the one looking. The declared counts on this page
                  come from the dictionary instead.
                </Footnote>
              </Card>
            </CardContent>
          </Card>

          {/* ================================== 3 · SENSITIVITY REVIEW QUEUE */}
          <Card data-testid="sensitivity-card">
            <CardHeader>
              <Lock className="size-3.5 text-muted-foreground" />
              <CardTitle>Sensitivity — the only thing that masks</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="mb-1 text-body text-muted-foreground" data-testid="sensitivity-statement">
                Masking is driven only by column-level{' '}
                <Identifier className="text-foreground">sensitivity</Identifier> in the data
                dictionary, and every entry is{' '}
                <span className="text-foreground">declared by a person</span>. There is no
                scanner — nothing is auto-detected, so a column nobody has declared is not
                masked, whatever it contains. That is why this queue ranks by{' '}
                <span className="text-foreground">undeclared</span>: it is the exposure.
              </p>
              <Footnote className="mb-3">
                Levels that mask, matched case-insensitively:{' '}
                {SENSITIVE_LEVELS.map((l, i) => (
                  <span key={l}>
                    {i > 0 && ' · '}
                    <Identifier className="text-muted-foreground">{l}</Identifier>
                  </span>
                ))}
                . Any other value is stored but inert.
              </Footnote>

              {scan.isError ? (
                <p className="py-4 text-center text-body text-muted-foreground">
                  {errorText(scan.error, { notFound: 'No datasets visible from this seat.' })}
                </p>
              ) : (
                <div className="overflow-hidden rounded-md">
                  <Table containerClassName="max-h-[420px]" data-testid="review-table">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Dataset</TableHead>
                        <TableHead className="text-right">Columns</TableHead>
                        <TableHead className="text-right">Masked</TableHead>
                        <TableHead className="text-right">Declared open</TableHead>
                        <TableHead className="text-right">Undeclared</TableHead>
                        <TableHead>Declared</TableHead>
                        <TableHead>Review</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>{(scan.data?.datasets ?? []).map(reviewRow)}</TableBody>
                  </Table>
                  {scan.isLoading && (
                    <p className="py-4 text-center text-body text-muted-foreground">
                      Reading every sheet's dictionary…
                    </p>
                  )}
                  {!scan.isLoading && (scan.data?.datasets.length ?? 0) === 0 && (
                    <p className="py-4 text-center text-body text-muted-foreground">
                      No datasets to review.
                    </p>
                  )}
                </div>
              )}

              <div className="mt-3 flex flex-wrap items-baseline gap-x-6 gap-y-1">
                <span className="text-body text-muted-foreground">
                  <span className="font-medium text-foreground tabular-nums">
                    {num(totals.masked)}
                  </span>{' '}
                  masked ·{' '}
                  <span className="font-medium text-foreground tabular-nums">
                    {num(totals.declaredOpen)}
                  </span>{' '}
                  declared open ·{' '}
                  <span className="font-medium text-foreground tabular-nums">
                    {num(totals.undeclared)}
                  </span>{' '}
                  undeclared, of{' '}
                  <span className="font-medium text-foreground tabular-nums">
                    {num(totals.columns)}
                  </span>{' '}
                  columns scanned
                </span>
                {totals.unreadable > 0 && (
                  <Footnote data-testid="unreadable-note">
                    {num(totals.unreadable)} dataset{totals.unreadable === 1 ? '' : 's'} returned
                    no answer from this seat — shown without a reason, because a 404 does not
                    give one.
                  </Footnote>
                )}
              </div>

              {inertLevels.length > 0 && (
                <Card nested className="mt-3" data-testid="inert-levels">
                  <Footnote className="leading-relaxed">
                    <span className="font-semibold text-foreground">
                      Declared, but not masking.
                    </span>{' '}
                    <Identifier className="text-foreground">sensitivity</Identifier> is free text
                    and is not validated, so a value outside the list above is stored and does
                    nothing — including{' '}
                    <Identifier className="text-foreground">internal</Identifier>, one of the
                    field's own documented examples. In scope right now:{' '}
                    {inertLevels.map(([level, count], i) => (
                      <span key={level}>
                        {i > 0 && ', '}
                        <Identifier className="text-foreground">{level}</Identifier> ({num(count)}
                        )
                      </span>
                    ))}
                    .
                  </Footnote>
                </Card>
              )}

              <Card nested className="mt-2" data-testid="filter-refusal-note">
                <Footnote className="leading-relaxed">
                  A masked column cannot be filtered, searched or sorted on —{' '}
                  <Identifier className="text-foreground">400 sensitive-column-not-filterable</Identifier>
                  . A steerable row count is a binary search over the hidden value, so the refusal
                  is the control holding, not a bug. The column can still be projected and read
                  masked.
                </Footnote>
              </Card>

              {scan.data && !scan.data.complete && (
                <Footnote className="mt-2" data-testid="scan-bound">
                  This seat can see {num(scan.data.total)} datasets; the first{' '}
                  {num(MAX_SCANNED_DATASETS)} were opened. The ranking covers only those — there
                  is no dataset-wide dictionary route, so each one costs a call per sheet.
                </Footnote>
              )}
            </CardContent>
          </Card>

          {/* ==================================== 4 · CLASSIFICATION LABELS */}
          {/* Drawn deliberately unlike the region above: no lock, no coverage
           * bar, no review status, no figures in the hero register. A label
           * that looks like a control IS the failure this screen guards. */}
          <Card data-testid="classification-card">
            <CardHeader>
              <CardTitle>Classification labels</CardTitle>
              <Footnote className="ml-auto">catalog metadata</Footnote>
            </CardHeader>
            <CardContent>
              <p className="mb-3 text-body text-muted-foreground" data-testid="classification-statement">
                <span className="font-medium text-foreground">
                  Classification is a label, not a control. It enforces nothing.
                </span>{' '}
                No read is refused, no download is blocked and no value is hidden because a
                dataset is labelled{' '}
                <Identifier className="text-foreground">restricted</Identifier>. It exists for
                findability, reporting and review queues. Masking comes only from column
                sensitivity, one column at a time — a dataset labelled{' '}
                <Identifier className="text-foreground">restricted</Identifier> with no column
                declared sensitive is fully readable by everyone who can read the dataset.
              </p>

              {facets.isError ? (
                <p className="py-2 text-body text-muted-foreground">{errorText(facets.error)}</p>
              ) : classifications.length === 0 ? (
                <p className="py-2 text-body text-muted-foreground">No labels in this catalog.</p>
              ) : (
                <div className="flex flex-wrap gap-x-8 gap-y-2" data-testid="classification-labels">
                  {classifications.map(([label, count]) => (
                    <div key={label} className="min-w-0" data-testid="classification-label">
                      <Identifier className="text-small text-muted-foreground">{label}</Identifier>
                      <Figure size="figure" className="mt-0.5">
                        {num(count)}
                      </Figure>
                    </div>
                  ))}
                </div>
              )}

              {labelOnly.length > 0 && (
                <Card nested className="mt-3" data-testid="label-only-note">
                  <Footnote className="leading-relaxed">
                    <span className="font-semibold text-foreground">
                      {num(labelOnly.length)} scanned dataset
                      {labelOnly.length === 1 ? ' is' : 's are'} labelled confidential or
                      restricted and declare{labelOnly.length === 1 ? 's' : ''} no sensitive
                      column.
                    </span>{' '}
                    Nothing is masked on{' '}
                    {labelOnly.length === 1 ? 'it' : 'them'}: {labelOnly.map((d) => d.name).join(', ')}.
                    The label is doing no work. Declaring the columns is what would.
                  </Footnote>
                </Card>
              )}
            </CardContent>
          </Card>

          {/* ======================================== 5 · THE QUALITY GATE */}
          <Card data-testid="quality-gate-card">
            <CardHeader>
              <CardTitle>The quality gate</CardTitle>
              <Footnote className="ml-auto">one gate, one place</Footnote>
            </CardHeader>
            <CardContent>
              <p className="text-body text-muted-foreground" data-testid="quality-gate-statement">
                <span className="font-medium text-foreground">
                  Tag promotion is the only quality gate in the system.
                </span>{' '}
                Uploading a file and publishing a version are ungated — nothing inspects the data
                on the way in, and no warning has ever stopped either. Promotion is the one place
                a rule can refuse: the target version must be{' '}
                <Identifier className="text-foreground">ready</Identifier>, and if the dataset has
                any enabled rule the version needs a completed validation run with zero
                error-level failures.
              </p>
              <Footnote className="mt-2 leading-relaxed">
                The refusals are{' '}
                <Identifier className="text-foreground">409 validation-required</Identifier> and{' '}
                <Identifier className="text-foreground">409 validation-failed</Identifier>. A
                rule's severity is a property of the rule, not a result:{' '}
                <Severity level="error">error</Severity>-level failures block a promotion and{' '}
                <Severity level="warning">warning</Severity>-level failures never do, however
                many there are. Both{' '}
                <Identifier className="text-foreground">PUT /datasets/{'{id}'}/tags</Identifier>{' '}
                and tag rollback stay ungated by design, so the gate is a checkpoint on one path
                rather than a wall around the data.
              </Footnote>
            </CardContent>
          </Card>

          {/* ================================ 6 · STORAGE AND RETENTION */}
          <Card data-testid="storage-card">
            <CardHeader>
              <CardTitle>Storage and retention</CardTitle>
              <Footnote className="ml-auto">collected only when asked</Footnote>
            </CardHeader>
            <CardContent>
              <p className="mb-3 text-body text-muted-foreground" data-testid="retention-statement">
                <span className="font-medium text-foreground">
                  There is no scheduler and no cron in this service, so nothing here expires on
                  its own.
                </span>{' '}
                A retention rule says how long an artifact is{' '}
                <span className="text-foreground">eligible</span> to be kept. It deletes nothing.
                Artifacts past their deadline stay in the object store and keep counting toward
                the total below until somebody presses{' '}
                <span className="text-foreground">Run sweep</span> — a person, once, now.
              </p>

              <div className="grid gap-5 md:grid-cols-2">
                <div className="min-w-0">
                  <Eyebrow>Object store in use</Eyebrow>
                  {usage.isError ? (
                    <p className="mt-1 text-body text-muted-foreground">{errorText(usage.error)}</p>
                  ) : (
                    <>
                      <Figure
                        size="hero"
                        unit={totalParts.unit}
                        className="mt-1"
                        data-testid="storage-total"
                      >
                        {totalParts.value}
                      </Figure>
                      <div className="mt-2 flex flex-col gap-1" data-testid="storage-breakdown">
                        {storageParts.map(([name, bytes]) => (
                          <Stat
                            key={name}
                            name={name}
                            value={formatBytes(bytes)}
                            coverage={coverage(bytes, totalBytes, 'bytes')}
                          />
                        ))}
                      </div>
                      {usage.isLoading && (
                        <Footnote className="mt-1">Reading the object store…</Footnote>
                      )}
                    </>
                  )}
                </div>

                <div className="min-w-0">
                  <Eyebrow>Retention rules</Eyebrow>
                  {retention.isError ? (
                    <p className="mt-1 text-body text-muted-foreground">
                      {errorText(retention.error)}
                    </p>
                  ) : (
                    <div className="mt-1 overflow-hidden rounded-md">
                      <Table containerClassName="max-h-[340px]" data-testid="retention-table">
                        <TableHeader>
                          <TableRow>
                            <TableHead>artifact_type</TableHead>
                            <TableHead className="text-right">Eligible for keeping</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {(retention.data?.rules ?? []).map((r) => (
                            <TableRow key={r.artifact_type} data-testid="retention-row">
                              <TableCell className="text-muted-foreground">
                                <Identifier className="text-small">{r.artifact_type}</Identifier>
                              </TableCell>
                              <TableNumericCell>
                                {r.retention_days == null ? (
                                  <span className="text-muted-foreground">indefinitely</span>
                                ) : (
                                  <span className="text-foreground">
                                    {num(r.retention_days)} days
                                  </span>
                                )}
                              </TableNumericCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                  {retention.data && (
                    <Footnote className="mt-1.5">
                      Orphans — blobs the catalog no longer references — become collectable{' '}
                      {num(retention.data.orphan_grace_hours)} hours after they appear. Becoming
                      collectable is not being collected.
                    </Footnote>
                  )}
                </div>
              </div>

              <div className="mt-4 flex flex-wrap items-end gap-6">
                <div className="min-w-0">
                  <Eyebrow>Past deadline, still stored</Eyebrow>
                  <Figure size="figure" className="mt-1" data-testid="expired-pending">
                    {retention.data ? num(retention.data.expired_pending) : '—'}
                  </Figure>
                  <Footnote>artifacts eligible for collection — not collected</Footnote>
                </div>
                <Button
                  onClick={() => gc.mutate()}
                  disabled={gc.isPending}
                  size="sm"
                  data-testid="run-gc"
                >
                  {gc.isPending ? 'Sweeping…' : 'Run sweep now'}
                </Button>
              </div>

              <Guard className="mt-2" data-testid="gc-guard">
                <span className="font-semibold text-foreground">
                  This button is the only thing that ever removes an expired artifact.
                </span>{' '}
                One sweep runs when it is pressed and then stops. It is bounded per call, so a
                large backlog needs several presses — and nothing re-runs it afterwards. A count of{' '}
                <Identifier className="text-foreground">expired_pending</Identifier> above zero
                means those artifacts are still in the object store right now.
              </Guard>

              {gc.data && (
                <Guard
                  tone={gc.data.more_remaining ? 'warning' : 'neutral'}
                  className="mt-2"
                  data-testid="gc-result"
                >
                  <span className="font-semibold text-foreground">Last sweep:</span>{' '}
                  <span className="tabular-nums text-foreground">
                    {num(gc.data.expired_deleted)}
                  </span>{' '}
                  expired and{' '}
                  <span className="tabular-nums text-foreground">
                    {num(gc.data.orphans_deleted)}
                  </span>{' '}
                  orphaned artifacts deleted,{' '}
                  <span className="tabular-nums text-foreground">
                    {formatBytes(gc.data.bytes_freed)}
                  </span>{' '}
                  freed.
                  {gcByType.length > 0 && (
                    <>
                      {' '}
                      By type:{' '}
                      {gcByType.map(([type, count], i) => (
                        <span key={type}>
                          {i > 0 && ', '}
                          <Identifier className="text-foreground">{type}</Identifier> ({num(count)})
                        </span>
                      ))}
                      .
                    </>
                  )}{' '}
                  {gc.data.more_remaining ? (
                    <>
                      <Status kind="warning">more remaining</Status> — the sweep stopped at its
                      per-call limit with collectable items left over. Press it again; nothing
                      else will.
                    </>
                  ) : (
                    <>
                      <Status kind="good">backlog reached</Status> — the pass got to the end of
                      the backlog, so this is the one case where zero deletions really does mean
                      nothing was due.
                    </>
                  )}
                </Guard>
              )}
            </CardContent>
          </Card>

          {/* ============================================== 7 · WEBHOOKS */}
          <Card data-testid="webhooks-card">
            <CardHeader>
              <CardTitle>Webhook subscriptions</CardTitle>
              <Footnote className="ml-auto">team-scoped</Footnote>
            </CardHeader>
            <CardContent>
              <p className="mb-3 text-body text-muted-foreground" data-testid="webhooks-statement">
                A subscription tells the service where to send a notification when something
                happens to this team's datasets. It is an{' '}
                <span className="text-foreground">annotation</span>: creating one grants no access
                to anything, and removing one destroys no rows. The signing secret is returned{' '}
                <span className="text-foreground">once</span>, when the subscription is created,
                and can never be read back — not by this screen, not by anyone.
              </p>

              {hooks.isError ? (
                <p className="py-4 text-center text-body text-muted-foreground">
                  {errorText(hooks.error, { notFound: 'No subscriptions visible from this seat.' })}
                </p>
              ) : (
                <div className="overflow-hidden rounded-md">
                  <Table containerClassName="max-h-[280px]" data-testid="webhooks-table">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>url</TableHead>
                        <TableHead>Events</TableHead>
                        <TableHead>State</TableHead>
                        <TableHead>Created</TableHead>
                        <TableHead>Manage</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(hooks.data?.items ?? []).map((w) => (
                        <TableRow
                          key={w.id}
                          data-testid="webhook-row"
                          data-state={selectedHook === w.id ? 'selected' : undefined}
                        >
                          <TableCell className="font-medium text-foreground">
                            <button
                              type="button"
                              className="text-left hover:underline"
                              onClick={() => setSelectedHook(selectedHook === w.id ? null : w.id)}
                              data-testid="select-webhook"
                            >
                              {w.name}
                            </button>
                          </TableCell>
                          <TableCell className="max-w-[220px] truncate text-muted-foreground">
                            <Identifier className="text-small" title={w.url}>
                              {w.url}
                            </Identifier>
                          </TableCell>
                          <TableCell className="max-w-[200px] truncate text-muted-foreground">
                            {w.events && w.events.length > 0 ? (
                              <Identifier className="text-small" title={w.events.join(', ')}>
                                {w.events.join(', ')}
                              </Identifier>
                            ) : (
                              <Footnote>all events</Footnote>
                            )}
                          </TableCell>
                          <TableCell>
                            <Status kind={w.enabled ? 'good' : 'unknown'}>
                              {w.enabled ? 'enabled' : 'disabled'}
                            </Status>
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            <Identifier className="text-small">
                              {shortDate(w.created_at)}
                            </Identifier>
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-1">
                              <Button
                                variant="ghost"
                                size="xs"
                                disabled={updateWebhook.isPending}
                                onClick={() =>
                                  updateWebhook.mutate({
                                    id: w.id,
                                    patch: { enabled: !w.enabled },
                                  })
                                }
                                data-testid="toggle-webhook"
                              >
                                {w.enabled ? 'Disable' : 'Enable'}
                              </Button>
                              <Button
                                variant="ghost"
                                size="xs"
                                disabled={testWebhook.isPending}
                                onClick={() => {
                                  setSelectedHook(w.id);
                                  testWebhook.mutate(w.id);
                                }}
                                data-testid="test-webhook"
                              >
                                Send real delivery
                              </Button>
                              <Button
                                variant="ghost"
                                size="xs"
                                onClick={() => setHookToDelete(w)}
                                data-testid="delete-webhook"
                              >
                                Delete
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {!hooks.isLoading && (hooks.data?.items.length ?? 0) === 0 && (
                    <p className="py-4 text-center text-body text-muted-foreground">
                      No subscriptions on this team.
                    </p>
                  )}
                </div>
              )}

              <Footnote className="mt-1.5" data-testid="test-delivery-note">
                <span className="font-semibold text-foreground">Send real delivery</span> is not a
                dry run. It POSTs to the subscription's url and records the attempt alongside every
                other delivery.
              </Footnote>

              {/* -------------------------------------------- create form */}
              <div className="mt-3 flex flex-wrap items-end gap-2" data-testid="webhook-form">
                <div className="min-w-[160px] flex-1">
                  <label className="block text-micro text-muted-foreground" htmlFor="webhook-name">
                    Name
                  </label>
                  <input
                    id="webhook-name"
                    value={hookName}
                    onChange={(e) => setHookName(e.target.value)}
                    aria-label="Subscription name"
                    className={fieldClass}
                    data-testid="webhook-name"
                  />
                </div>
                <div className="min-w-[240px] flex-[2]">
                  <label className="block text-micro text-muted-foreground" htmlFor="webhook-url">
                    url
                  </label>
                  <input
                    id="webhook-url"
                    value={hookUrl}
                    onChange={(e) => setHookUrl(e.target.value)}
                    aria-label="Subscription url"
                    className={fieldClass}
                    data-testid="webhook-url"
                  />
                </div>
                <label className="flex h-6 items-center gap-1.5 text-small text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={hookEnabled}
                    onChange={(e) => setHookEnabled(e.target.checked)}
                    data-testid="webhook-enabled"
                  />
                  enabled
                </label>
                <Button
                  size="xs"
                  onClick={submitWebhook}
                  disabled={
                    createWebhook.isPending || hookName.trim() === '' || hookUrl.trim() === ''
                  }
                  data-testid="create-webhook"
                >
                  {createWebhook.isPending ? 'Creating…' : 'Create subscription'}
                </Button>
              </div>

              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1" data-testid="webhook-events">
                {WEBHOOK_EVENTS.map((e) => (
                  <label
                    key={e}
                    className="flex items-center gap-1.5 text-small text-muted-foreground"
                  >
                    <input
                      type="checkbox"
                      checked={hookEvents.includes(e)}
                      onChange={() => toggleEvent(e)}
                    />
                    <Identifier className="text-small">{e}</Identifier>
                  </label>
                ))}
              </div>
              <Footnote className="mt-1">
                Select none to receive every event. A filter is stored verbatim and nothing
                re-checks it afterwards, so only the seven names above are offered — one that does
                not exist would leave a subscription that looks healthy and can never fire.
              </Footnote>

              {createWebhook.data && (
                <Guard tone="warning" className="mt-2" data-testid="webhook-secret">
                  <span className="font-semibold text-foreground">
                    Signing secret for “{createWebhook.data.name}” — shown once.
                  </span>{' '}
                  <Identifier className="text-foreground break-all">
                    {createWebhook.data.secret}
                  </Identifier>{' '}
                  Store it now; it cannot be retrieved, and neither the list above nor the detail
                  read below will ever return it. Subscription{' '}
                  <Identifier className="text-foreground">{createWebhook.data.id}</Identifier> on
                  team <Identifier className="text-foreground">{createWebhook.data.team_id}</Identifier>.
                </Guard>
              )}

              {/* ------------------------------------ selection + deliveries */}
              {selectedHook && (
                <Card nested className="mt-3" data-testid="webhook-detail">
                  {hookDetail.isError ? (
                    <p className="text-body text-muted-foreground">
                      {errorText(hookDetail.error, {
                        notFound: 'That subscription is not available to this seat.',
                      })}
                    </p>
                  ) : selectedWebhook ? (
                    <>
                      <div className="flex flex-wrap gap-x-6 gap-y-1.5">
                        <Detail label="Name">{selectedWebhook.name}</Detail>
                        <Detail label="id">
                          <Identifier className="text-small">{selectedWebhook.id}</Identifier>
                        </Detail>
                        <Detail label="team_id">
                          <Identifier className="text-small">{selectedWebhook.team_id}</Identifier>
                        </Detail>
                        <Detail label="url">
                          <Identifier className="text-small">{selectedWebhook.url}</Identifier>
                        </Detail>
                        <Detail label="Events">
                          {selectedWebhook.events && selectedWebhook.events.length > 0 ? (
                            <Identifier className="text-small">
                              {selectedWebhook.events.join(', ')}
                            </Identifier>
                          ) : (
                            'all events'
                          )}
                        </Detail>
                        <Detail label="created_by">
                          <Identifier className="text-small">
                            {selectedWebhook.created_by ?? '—'}
                          </Identifier>
                        </Detail>
                        <Detail label="updated_at">
                          <Identifier className="text-small">
                            {shortDate(selectedWebhook.updated_at)}{' '}
                            {clockTime(selectedWebhook.updated_at)}
                          </Identifier>
                        </Detail>
                      </div>

                      <div className="mt-2 overflow-hidden rounded-md">
                        <Table containerClassName="max-h-[300px]" data-testid="deliveries-table">
                          <TableHeader>
                            <TableRow>
                              <TableHead>When</TableHead>
                              <TableHead>event_type</TableHead>
                              <TableHead>dataset_id</TableHead>
                              <TableHead>Delivery</TableHead>
                              <TableHead className="text-right">Attempts</TableHead>
                              <TableHead className="text-right">Answered</TableHead>
                              <TableHead>Delivered</TableHead>
                              <TableHead>error</TableHead>
                              <TableHead>payload</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {(deliveries.data?.items ?? []).map((d) => (
                              <TableRow key={d.id} data-testid="delivery-row">
                                <TableCell className="text-muted-foreground">
                                  <Identifier className="text-small">
                                    {shortDate(d.created_at)} {clockTime(d.created_at)}
                                  </Identifier>
                                </TableCell>
                                <TableCell className="text-muted-foreground">
                                  <Identifier className="text-small">{d.event_type}</Identifier>
                                </TableCell>
                                <TableCell className="max-w-[160px] truncate text-muted-foreground">
                                  <Identifier className="text-small">
                                    {d.dataset_id ?? '—'}
                                  </Identifier>
                                </TableCell>
                                <TableCell>
                                  <Status kind={deliveryStatusKind(d.status)}>{d.status}</Status>
                                </TableCell>
                                <TableNumericCell className="text-muted-foreground">
                                  {num(d.attempts)}
                                </TableNumericCell>
                                <TableNumericCell>
                                  <HttpStatus code={d.response_status} />
                                </TableNumericCell>
                                <TableCell className="text-muted-foreground">
                                  <Identifier className="text-small">
                                    {clockTime(d.delivered_at)}
                                  </Identifier>
                                </TableCell>
                                <TableCell className="max-w-[200px] truncate text-muted-foreground">
                                  {d.error ? (
                                    <span title={d.error}>{d.error}</span>
                                  ) : (
                                    <span className="text-muted-foreground">—</span>
                                  )}
                                </TableCell>
                                <TableCell>
                                  {d.payload ? (
                                    <Popover>
                                      <PopoverTrigger className="text-footnote text-muted-foreground underline underline-offset-2 hover:text-foreground">
                                        view
                                      </PopoverTrigger>
                                      <PopoverContent align="end" className="w-96">
                                        <JsonViewer value={d.payload} maxHeight="240px" />
                                      </PopoverContent>
                                    </Popover>
                                  ) : (
                                    <span className="text-muted-foreground">—</span>
                                  )}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                        {!deliveries.isLoading && (deliveries.data?.items.length ?? 0) === 0 && (
                          <p className="py-4 text-center text-body text-muted-foreground">
                            Nothing has been delivered on this subscription yet.
                          </p>
                        )}
                      </div>
                      <Footnote className="mt-1">
                        The most recent {num(OPS_PAGE_LIMIT)} of {num(deliveries.data?.total ?? 0)}{' '}
                        recorded attempts.
                      </Footnote>
                    </>
                  ) : (
                    <p className="text-body text-muted-foreground">Reading the subscription…</p>
                  )}
                </Card>
              )}
            </CardContent>
          </Card>

          {/* ============================================== 8 · AUDIT LOG */}
          <Card data-testid="audit-card">
            <CardHeader>
              <CardTitle>Audit log</CardTitle>
              <Footnote className="ml-auto">newest first</Footnote>
            </CardHeader>
            <CardContent>
              <p className="mb-3 text-body text-muted-foreground" data-testid="audit-statement">
                What the service recorded: who acted, what they called, and what it answered. This
                is a record, not a control — nothing on this page was prevented by it.{' '}
                <span className="text-foreground">
                  Every mutation is recorded, and the only reads recorded are the ones that take
                  data out — <span className="font-mono">download</span> and artifact fetches.
                </span>{' '}
                An ordinary <span className="font-mono">GET</span> leaves no entry, so the absence
                of a row is not evidence that nothing was looked at. A cross-tenant call that IS
                recorded appears as the 404 it was, because the log draws no distinction between
                "absent" and "not yours" any more than the API does.
              </p>

              {audit.isError ? (
                <p className="py-4 text-center text-body text-muted-foreground">
                  {errorText(audit.error, { notFound: 'No audit entries available to this seat.' })}
                </p>
              ) : (
                <div className="overflow-hidden rounded-md">
                  <Table containerClassName="max-h-[420px]" data-testid="audit-table">
                    <TableHeader>
                      <TableRow>
                        <TableHead>When</TableHead>
                        <TableHead>Actor</TableHead>
                        <TableHead>method</TableHead>
                        <TableHead>path</TableHead>
                        <TableHead className="text-right">Answered</TableHead>
                        <TableHead className="text-right">ms</TableHead>
                        <TableHead>Resource</TableHead>
                        <TableHead>Detail</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {auditItems.map((a) => (
                        <TableRow key={a.id} data-testid="audit-row">
                          <TableCell className="text-muted-foreground">
                            <Identifier className="text-small">
                              {shortDate(a.occurred_at)} {clockTime(a.occurred_at)}
                            </Identifier>
                          </TableCell>
                          <TableCell className="max-w-[150px] truncate text-muted-foreground">
                            <Identifier className="text-small">
                              {a.actor_email ?? a.actor_user_id ?? '—'}
                            </Identifier>
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            <Identifier className="text-small">{a.method}</Identifier>
                          </TableCell>
                          <TableCell className="max-w-[200px] truncate text-muted-foreground">
                            <Identifier className="text-small" title={a.path}>
                              {a.path}
                            </Identifier>
                          </TableCell>
                          <TableNumericCell>
                            <HttpStatus code={a.status_code} />
                          </TableNumericCell>
                          <TableNumericCell className="text-muted-foreground">
                            {a.duration_ms == null ? '—' : num(a.duration_ms)}
                          </TableNumericCell>
                          <TableCell className="max-w-[140px] truncate text-muted-foreground">
                            {a.resource_type ? (
                              <Identifier className="text-small">
                                {a.resource_type}
                                {a.resource_id ? ` · ${a.resource_id}` : ''}
                              </Identifier>
                            ) : (
                              '—'
                            )}
                          </TableCell>
                          <TableCell>
                            <Popover>
                              <PopoverTrigger className="text-footnote text-muted-foreground underline underline-offset-2 hover:text-foreground">
                                open
                              </PopoverTrigger>
                              <PopoverContent align="end" className="w-96">
                                <div className="flex flex-col gap-1.5">
                                  <Detail label="action">
                                    <Identifier className="text-small">{a.action}</Identifier>
                                  </Detail>
                                  <Detail label="request_id">
                                    <Identifier className="text-small">
                                      {a.request_id ?? '—'}
                                    </Identifier>
                                  </Detail>
                                  <Detail label="team_id">
                                    <Identifier className="text-small">
                                      {a.team_id ?? '—'}
                                    </Identifier>
                                  </Detail>
                                  <Detail label="actor_user_id">
                                    <Identifier className="text-small">
                                      {a.actor_user_id ?? '—'}
                                    </Identifier>
                                  </Detail>
                                  <Detail label="ip">
                                    <Identifier className="text-small">{a.ip ?? '—'}</Identifier>
                                  </Detail>
                                  <Detail label="user_agent">{a.user_agent ?? '—'}</Detail>
                                  {a.metadata ? (
                                    <div className="min-w-0">
                                      <Eyebrow>metadata</Eyebrow>
                                      <JsonViewer value={a.metadata} maxHeight="180px" />
                                    </div>
                                  ) : (
                                    <Detail label="metadata">none recorded</Detail>
                                  )}
                                </div>
                              </PopoverContent>
                            </Popover>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {!audit.isLoading && auditItems.length === 0 && (
                    <p className="py-4 text-center text-body text-muted-foreground">
                      No entries on this page.
                    </p>
                  )}
                </div>
              )}

              <div className="mt-2 flex flex-wrap items-center gap-3">
                <Button
                  variant="ghost"
                  size="xs"
                  disabled={auditOffset === 0 || audit.isFetching}
                  onClick={() => setAuditOffset(Math.max(0, auditOffset - OPS_PAGE_LIMIT))}
                  data-testid="audit-newer"
                >
                  Newer
                </Button>
                <Button
                  variant="ghost"
                  size="xs"
                  disabled={auditLast >= auditTotal || audit.isFetching}
                  onClick={() => setAuditOffset(auditOffset + OPS_PAGE_LIMIT)}
                  data-testid="audit-older"
                >
                  Older
                </Button>
                <Footnote data-testid="audit-range">
                  {num(auditFirst)}–{num(auditLast)} of {num(auditTotal)} recorded
                </Footnote>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* ------------------------------------------------- confirmations */}
      <AlertDialog
        open={hookToDelete !== null}
        onOpenChange={(open) => {
          if (!open) setHookToDelete(null);
        }}
      >
        <AlertDialogContent data-testid="delete-webhook-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this subscription?</AlertDialogTitle>
            <AlertDialogDescription>
              “{hookToDelete?.name}” stops receiving notifications, and its recorded deliveries go
              with it. No dataset, version or row is touched — you can create the subscription
              again, though it will be issued a new signing secret.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleteWebhook.isPending}
              onClick={() => {
                const target = hookToDelete;
                if (!target) return;
                if (selectedHook === target.id) setSelectedHook(null);
                deleteWebhook.mutate(target.id);
                setHookToDelete(null);
              }}
              data-testid="confirm-delete-webhook"
            >
              {deleteWebhook.isPending ? 'Removing…' : 'Remove'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={memberToRemove !== null}
        onOpenChange={(open) => {
          if (!open) setMemberToRemove(null);
        }}
      >
        <AlertDialogContent data-testid="remove-member-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this membership?</AlertDialogTitle>
            <AlertDialogDescription>
              {memberToRemove?.name} loses access to {team?.team_name ?? 'this team'} and its
              datasets, which then answer 404 rather than 403. The user record itself is untouched,
              as is every dataset, version and row — and they can be added back by email.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={removeMember.isPending}
              onClick={() => {
                const target = memberToRemove;
                if (!target) return;
                removeMember.mutate(target.user_id);
                setMemberToRemove(null);
              }}
              data-testid="confirm-remove-member"
            >
              {removeMember.isPending ? 'Removing…' : 'Remove'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
