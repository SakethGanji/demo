/** Meridian — banking operations showcase. Real API data; no illustrative metrics. */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { api, OpsSummaryResponse, OpsTrendsResponse, OpsTransactionsResponse, OpsAccountsResponse, OpsCasesResponse, OpsTransactionResponse } from './api/client';

/** Format numbers as USD currency strings */
const formatUSD = (amount?: number): string =>
  typeof amount === 'number' ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount) : '—';

/** Primary entry component for Meridian Operations */
export default function App() {
  const [activeNav, setActiveNav] = useState<'overview' | 'transactions' | 'accounts' | 'exceptions'>('overview');

  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [accountSearch, setAccountSearch] = useState('');
  const [accountSort, setAccountSort] = useState(false);
  const [caseFilter, setCaseFilter] = useState('all');
  const [caseSearch, setCaseSearch] = useState('');
  const [hoveredDay, setHoveredDay] = useState<number | null>(null);
  const detailRequest = useRef(0);
  const drawerRef = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  // Summary state
  const [summary, setSummary] = useState<OpsSummaryResponse | null>(null);
  const [loadingSummary, setLoadingSummary] = useState<boolean>(false);
  const [errorSummary, setErrorSummary] = useState<string | null>(null);

  // Overview data
  const [trends, setTrends] = useState<OpsTrendsResponse['items']>([]);
  const [overviewTxs, setOverviewTxs] = useState<OpsTransactionsResponse['items']>([]);
  const [overviewCases, setOverviewCases] = useState<OpsCasesResponse['items']>([]);
  const [loadingOverview, setLoadingOverview] = useState<boolean>(false);
  const [errorOverview, setErrorOverview] = useState<string | null>(null);

  // Transactions filters & state
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [regionFilter, setRegionFilter] = useState<string>('all');
  const [searchInput, setSearchInput] = useState<string>('');
  const [appliedFilters, setAppliedFilters] = useState<{ status: string; region: string; q: string }>({ status: 'all', region: 'all', q: '' });
  const [page, setPage] = useState<number>(1);
  const [txsData, setTxsData] = useState<OpsTransactionsResponse>({ items: [], total: 0, page: 1, page_size: 25 });
  const [loadingTxs, setLoadingTxs] = useState<boolean>(false);
  const [errorTxs, setErrorTxs] = useState<string | null>(null);
  const txReqRef = useRef<number>(0);

  // Accounts state
  const [accounts, setAccounts] = useState<OpsAccountsResponse['items']>([]);
  const [loadingAccounts, setLoadingAccounts] = useState<boolean>(false);
  const [errorAccounts, setErrorAccounts] = useState<string | null>(null);

  // Exceptions state
  const [cases, setCases] = useState<OpsCasesResponse['items']>([]);
  const [loadingCases, setLoadingCases] = useState<boolean>(false);
  const [errorCases, setErrorCases] = useState<string | null>(null);

  // Detail Modal state
  const [selectedTxId, setSelectedTxId] = useState<string | null>(null);
  const [detailData, setDetailData] = useState<OpsTransactionResponse | null>(null);
  const [loadingDetail, setLoadingDetail] = useState<boolean>(false);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);

  // Fetch summary
  const fetchSummary = useCallback(async () => {
    setLoadingSummary(true);
    setErrorSummary(null);
    try {
      const res = await api.opsSummary();
      setSummary(res);
      setUpdatedAt(new Date());
    } catch (err: any) {
      setErrorSummary(err.message || 'Failed to fetch summary');
    } finally {
      setLoadingSummary(false);
    }
  }, []);

  // Fetch Overview Data
  const fetchOverview = useCallback(async () => {
    setLoadingOverview(true);
    setErrorOverview(null);
    try {
      const [trendRes, txRes, caseRes] = await Promise.all([
        api.opsTrends(),
        api.opsTransactions({ page: 1, page_size: 5 }),
        api.opsCases({ limit: 5 })
      ]);
      setTrends(trendRes.items || []);
      setOverviewTxs(txRes.items || []);
      setOverviewCases((caseRes.items || []).slice(0, 5));
    } catch (err: any) {
      setErrorOverview(err.message || 'Failed to fetch overview data');
    } finally {
      setLoadingOverview(false);
    }
  }, []);

  // Fetch Transactions Data with stale-request race condition guard
  const fetchTransactions = useCallback(async (p: number, filters: { status: string; region: string; q: string }) => {
    const reqId = ++txReqRef.current;
    setLoadingTxs(true);
    setErrorTxs(null);
    try {
      const query: Record<string, any> = { page: p, page_size: 25 };
      if (filters.status !== 'all') query.status = filters.status;
      if (filters.region !== 'all') query.region = filters.region;
      if (filters.q.trim()) {
        query.q = filters.q.trim();
        query.search = filters.q.trim();
      }
      const res = await api.opsTransactions(query);
      if (reqId === txReqRef.current) {
        setTxsData(res);
      }
    } catch (err: any) {
      if (reqId === txReqRef.current) {
        setErrorTxs(err.message || 'Failed to load transactions');
      }
    } finally {
      if (reqId === txReqRef.current) {
        setLoadingTxs(false);
      }
    }
  }, []);

  // Fetch Accounts Data
  const fetchAccounts = useCallback(async () => {
    setLoadingAccounts(true);
    setErrorAccounts(null);
    try {
      const res = await api.opsAccounts();
      setAccounts(res.items || []);
    } catch (err: any) {
      setErrorAccounts(err.message || 'Failed to load accounts');
    } finally {
      setLoadingAccounts(false);
    }
  }, []);

  // Fetch Cases Data
  const fetchCases = useCallback(async () => {
    setLoadingCases(true);
    setErrorCases(null);
    try {
      const res = await api.opsCases();
      setCases(res.items || []);
    } catch (err: any) {
      setErrorCases(err.message || 'Failed to load exceptions');
    } finally {
      setLoadingCases(false);
    }
  }, []);

  // Single Transaction Detail Fetch
  const openDetail = useCallback(async (id: string) => {
    const request = ++detailRequest.current;
    returnFocus.current = document.activeElement as HTMLElement;
    setSelectedTxId(id);
    setDetailData(null);
    setLoadingDetail(true);
    setErrorDetail(null);
    try {
      const res = await api.opsTransaction({ id });
      if (request === detailRequest.current) setDetailData(res);
    } catch (err: any) {
      if (request === detailRequest.current) setErrorDetail(err.message || 'Failed to load transaction details');
    } finally {
      if (request === detailRequest.current) setLoadingDetail(false);
    }
  }, []);

  const closeDetail = useCallback(() => {
    detailRequest.current++;
    setSelectedTxId(null);
    setDetailData(null);
    returnFocus.current?.focus();
  }, []);

  useEffect(() => {
    if (!selectedTxId) return;
    drawerRef.current?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const nodes = drawerRef.current?.querySelectorAll<HTMLElement>('button, a, input, select, [tabindex="0"]');
      if (!nodes?.length) return;
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === drawerRef.current)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', trap);
    return () => window.removeEventListener('keydown', trap);
  }, [selectedTxId]);

  // Keyboard escape handler for detail drawer
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selectedTxId) {
        closeDetail();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedTxId, closeDetail]);

  // Initial load & Nav change triggers
  useEffect(() => {
    fetchSummary();
  }, [fetchSummary]);

  useEffect(() => {
    if (activeNav === 'overview') { fetchOverview(); fetchAccounts(); }
    if (activeNav === 'transactions') fetchTransactions(page, appliedFilters);
    if (activeNav === 'accounts') fetchAccounts();
    if (activeNav === 'exceptions') fetchCases();
  }, [activeNav, fetchOverview, fetchTransactions, fetchAccounts, fetchCases, page, appliedFilters]);

  // Apply filters handler
  const handleApplyFilters = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const newFilters = { status: statusFilter, region: regionFilter, q: searchInput };
    setAppliedFilters(newFilters);
    setPage(1);
  };

  // Refresh current view + summary
  const handleRefresh = () => {
    fetchSummary();
    if (activeNav === 'overview') { fetchOverview(); fetchAccounts(); }
    if (activeNav === 'transactions') fetchTransactions(page, appliedFilters);
    if (activeNav === 'accounts') fetchAccounts();
    if (activeNav === 'exceptions') fetchCases();
  };

  const nav = [
    { id: 'overview', label: 'Overview', icon: 'grid' },
    { id: 'transactions', label: 'Transactions', icon: 'arrows' },
    { id: 'accounts', label: 'Accounts', icon: 'wallet' },
    { id: 'exceptions', label: 'Exceptions', icon: 'flag' },
  ] as const;
  const active = nav.find(item => item.id === activeNav)!;
  const settled = summary ? summary.transactions - summary.pending - summary.exceptions : 0;
  const settledPercent = summary?.transactions ? settled / summary.transactions * 100 : 0;
  const maxVolume = Math.max(...trends.map(t => t.volume), 1);
  const averageVolume = trends.length ? trends.reduce((sum, t) => sum + t.volume, 0) / trends.length : undefined;
  const day = trends[hoveredDay ?? trends.length - 1];
  const regions = accounts.reduce<Record<string, { balance: number; count: number }>>((result, account) => {
    const region = account.region;
    result[region] ??= { balance: 0, count: 0 };
    result[region].balance += account.balance;
    result[region].count++;
    return result;
  }, {});
  const totalBalance = accounts.reduce((total, item) => total + item.balance, 0);
  const filteredAccounts = accounts.filter(account => `${account.name} ${account.branch_name} ${account.region}`.toLowerCase().includes(accountSearch.toLowerCase()));
  if (accountSort) filteredAccounts.sort((a, b) => b.balance - a.balance);
  const filteredCases = cases.filter(item => (caseFilter === 'all' || item.severity === caseFilter) && `${item.id} ${item.account_name} ${item.reason} ${item.owner}`.toLowerCase().includes(caseSearch.toLowerCase()));
  const error = errorSummary || (activeNav === 'overview' ? errorOverview || errorAccounts : activeNav === 'transactions' ? errorTxs : activeNav === 'accounts' ? errorAccounts : errorCases);
  const date = (value: string) => new Date(value.slice(0, 10) + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const compact = (value?: number) => value == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 2 }).format(value);
  const initials = (name: string) => name.split(' ').filter(Boolean).map(part => part[0]).slice(-2).join('');
  const badge = (value: string) => <span className={`m-badge ${value}`}><i />{value}</span>;
  const exportPage = () => {
    const fields = ['id', 'account_name', 'amount', 'status', 'region', 'booked_at'] as const;
    const quote = (value: unknown) => '"' + String(value ?? '').replace(/^[=+@-]/, "'$&").replace(/"/g, '""') + '"';
    const blob = new Blob([[fields.join(','), ...txsData.items.map(row => fields.map(field => quote(row[field])).join(','))].join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = `meridian-transactions-page-${page}.csv`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const transactionTable = (items: OpsTransactionsResponse['items'], full = false) => <div className="m-table-scroll"><table className="m-table"><thead><tr><th>Transaction / client</th>{full && <th>Booked</th>}<th>Status</th>{full && <th>Region</th>}<th className="number">Amount</th><th><span className="m-sr">Actions</span></th></tr></thead><tbody>{items.map(item => <tr key={item.id} data-testid={full ? 'transaction-row' : undefined} data-id={item.id}>
    <td><div className="m-client"><span className="m-avatar">{initials(item.account_name)}</span><span><button className="m-link" onClick={() => openDetail(item.id)}>{item.id}</button><small>{item.account_name}</small></span></div></td>
    {full && <td className="muted">{date(item.booked_at)}</td>}<td>{badge(item.status)}</td>{full && <td className="muted">{item.region}</td>}<td className="number m-money">{formatUSD(item.amount)}</td><td><button className="m-icon-button" aria-label={`View details ${item.id}`} onClick={() => openDetail(item.id)}><Icon name="chevron" /></button></td>
  </tr>)}</tbody></table></div>;

  return <div className="meridian" data-theme={theme}>
    <style>{styles}</style>
    <aside className="m-sidebar">
      <a className="m-brand" href="#" onClick={event => { event.preventDefault(); setActiveNav('overview'); }}><span className="m-brand-symbol"><Icon name="brand" /></span><span>meridian<span className="m-brand-period">.</span><small>OPERATIONS WORKSPACE</small></span></a>
      <div className="m-workspace"><span className="m-workspace-icon">M</span><span>Meridian Capital<small>Banking operations</small></span><Icon name="chevron" /></div>
      <div className="m-nav-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{nav.map(item => <button key={item.id} data-testid={`nav-${item.id}`} aria-label={item.label} className={`m-nav ${activeNav === item.id ? 'active' : ''}`} aria-current={activeNav === item.id ? 'page' : undefined} onClick={() => setActiveNav(item.id)}><Icon name={item.icon} /><span>{item.label}</span>{item.id === 'exceptions' && summary && <b>{summary.exceptions}</b>}</button>)}</nav>
      <div className="m-sidebar-note"><span className="m-note-icon"><Icon name="shield" /></span><strong>Your data. In focus.</strong><p>A connected view of transactions, clients and operational risk.</p><span className="m-outline-label">READ-ONLY ACCESS</span></div>
      <div className="m-sidebar-bottom"><span className="m-user">MC</span><span>Operations team<small>Synthetic demo workspace</small></span><span className="m-online-dot" /></div>
    </aside>
    <section className="m-body">
      <header className="m-topbar"><div className="m-breadcrumb">Workspace <span>/</span> <strong>{active.label}</strong></div><div className="m-top-actions"><span className={`m-connection ${errorSummary ? 'offline' : ''}`}><i />{errorSummary ? 'Connection interrupted' : !summary ? 'Connecting' : 'Database connected'}</span><button className="m-icon-button" data-testid="theme-toggle" aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}><Icon name={theme === 'light' ? 'moon' : 'sun'} /></button><span className="m-user small">MC</span></div></header>
      <main className="m-main">
        <div className="m-page-heading"><div><div className="m-eyebrow">MERIDIAN INTELLIGENCE <span> / </span> {activeNav === 'overview' ? 'AT A GLANCE' : 'OPERATIONS'}</div><h1>{activeNav === 'overview' ? 'Every detail. One clear view' : active.label}<span className="m-title-dot">.</span></h1><p>{activeNav === 'overview' ? 'Your operational pulse, connected directly to your data.' : activeNav === 'transactions' ? 'Follow every movement, from booking to resolution.' : activeNav === 'accounts' ? 'A closer look at your client portfolio and regional exposure.' : 'Prioritize what needs attention. See the complete context.'}</p></div><div className="m-heading-actions"><button className="m-button" data-testid="app-refresh" onClick={handleRefresh} disabled={loadingSummary}><Icon name="refresh" />{loadingSummary ? 'Refreshing' : 'Refresh'}</button>{activeNav === 'transactions' ? <button className="m-button primary" onClick={exportPage} disabled={loadingTxs || !txsData.items.length}><Icon name="download" />Export page</button> : <button className="m-button primary" onClick={() => setActiveNav('transactions')}>View transactions<Icon name="arrow" /></button>}</div></div>
        <div className="m-context-line"><span><i className="m-online-dot" />{updatedAt ? `Updated ${updatedAt.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}` : 'Waiting for data'}</span><span>USD · Synthetic demonstration data</span></div>
        {error && <div className="m-error" role="alert"><Icon name="flag" /><span><strong>We couldn’t refresh this view.</strong><small>{error}</small></span><button className="m-button" onClick={handleRefresh}>Retry</button></div>}
        <section className="m-kpis" aria-label="Operational summary">
          <article className="m-kpi featured"><div><span>Total transaction volume</span><Icon name="arrows" /></div><strong>{compact(summary?.volume)}</strong><small>{formatUSD(summary?.volume)} across all transactions</small><svg className="m-spark" viewBox="0 0 140 40" aria-hidden="true"><polyline points={trends.map((item, index) => `${index * 10},${38 - item.volume / maxVolume * 32}`).join(' ')} fill="none" stroke="currentColor" strokeWidth="2" /></svg></article>
          <article className="m-kpi"><div><span>Total transactions</span><Icon name="arrows" /></div><strong data-testid="kpi-transactions">{summary?.transactions ?? '—'}</strong><small><span className="m-positive">{summary ? settled.toLocaleString() : '—'} settled</span> · {summary?.pending ?? '—'} pending</small></article>
          <article className="m-kpi"><div><span>Settlement rate</span><Icon name="check" /></div><strong>{summary ? settledPercent.toFixed(1) + '%' : '—'}</strong><small>Of all booked transactions</small><div className="m-progress"><span style={{ width: `${settledPercent}%` }} /></div></article>
          <article className="m-kpi"><div><span>Needs attention</span><span className="m-attention-icon"><Icon name="flag" /></span></div><strong data-testid="kpi-exceptions">{summary?.exceptions ?? '—'}</strong><small><button className="m-link" onClick={() => setActiveNav('exceptions')}>Open exception workspace <span>↗</span></button></small></article>
        </section>

        {activeNav === 'overview' && <>
          <div className="m-chart-grid">
            <section className="m-card m-volume"><div className="m-card-heading"><div><h2>Transaction volume</h2><p>{trends.length ? `${date(trends[0].day)} – ${date(trends[trends.length - 1].day)}` : 'Daily activity'}</p></div><span className="m-chip"><i />Daily volume · USD</span></div>
              <div className="m-chart-stats"><div><span>Daily average</span><strong>{compact(averageVolume)}</strong></div><div><span>{day ? date(day.day) : 'Latest day'}</span><strong>{compact(day?.volume)}</strong></div><span className="m-chart-count">{trends.length} days of activity</span></div>
              {loadingOverview ? <Loading label="Loading volume history" /> : <div data-testid="volume-chart" className="m-volume-chart" role="group" aria-label="Daily transaction volume in USD"><div className="m-y-labels">{[1, .75, .5, .25, 0].map(value => <span key={value}>{compact(maxVolume * value)}</span>)}</div><div className="m-plot"><div className="m-gridlines">{[0, 1, 2, 3, 4].map(value => <i key={value} />)}</div><div className="m-bars">{trends.map((item, index) => <button key={item.day} data-testid="trend-bar" className={index === (hoveredDay ?? trends.length - 1) ? 'selected' : ''} style={{ height: `${Math.max(2, item.volume / maxVolume * 100)}%` }} aria-label={`${date(item.day)}: ${formatUSD(item.volume)}`} title={`${date(item.day)} · ${formatUSD(item.volume)}`} onMouseEnter={() => setHoveredDay(index)} onMouseLeave={() => setHoveredDay(null)} onFocus={() => setHoveredDay(index)} onBlur={() => setHoveredDay(null)} />)}</div><div className="m-x-labels">{trends.filter((_, index) => index % 3 === 0 || index === trends.length - 1).map(item => <span key={item.day}>{date(item.day)}</span>)}</div></div></div>}
            </section>
            <section className="m-card"><div className="m-card-heading"><div><h2>Transaction health</h2><p>Every transaction accounted for</p></div><Icon name="shield" /></div><div className="m-health"><div className="m-donut" style={{ background: `conic-gradient(var(--green) 0 ${settledPercent}%, var(--amber) ${settledPercent}% ${summary?.transactions ? (settled + summary.pending) / summary.transactions * 100 : 0}%, var(--red) 0 100%)` }}><div><strong>{summary?.transactions ?? '—'}</strong><small>TRANSACTIONS</small></div></div><div className="m-health-legend">{[['Settled', settled, 'green'], ['Pending', summary?.pending, 'amber'], ['Exception', summary?.exceptions, 'red']].map(([label, value, color]) => <div key={String(label)}><span><i style={{ background: `var(--${color})` }} />{label}</span><strong>{summary ? value : '—'}</strong></div>)}</div></div><div className="m-health-note"><Icon name="flag" /><span>{summary?.exceptions ?? '—'} transactions require review<button onClick={() => setActiveNav('exceptions')}>Review exceptions <span>→</span></button></span></div></section>
          </div>
          <div className="m-lower-grid"><section className="m-card"><div className="m-card-heading"><div><h2>Latest transactions</h2><p>The most recently booked activity</p></div><button className="m-text-button" onClick={() => setActiveNav('transactions')}>View all <Icon name="arrow" /></button></div>{loadingOverview ? <Loading /> : transactionTable(overviewTxs)}</section><section className="m-card"><div className="m-card-heading"><div><h2>Priority queue</h2><p>Recent exceptions, ready for review</p></div><span className="m-count">{summary?.exceptions ?? '—'}</span></div><div className="m-queue">{overviewCases.slice(0, 3).map(item => <button key={item.id} onClick={() => openDetail(item.transaction_id)}><span className={`m-queue-mark ${item.severity}`}><Icon name="flag" /></span><span><strong>{item.reason}</strong><small>{item.account_name} · {item.id}</small></span><span>{badge(item.severity)}<small>{formatUSD(item.amount)}</small></span></button>)}</div><button className="m-card-footer" onClick={() => setActiveNav('exceptions')}>Open exception workspace <Icon name="arrow" /></button></section></div>
          <section className="m-card m-portfolio"><div className="m-card-heading"><div><h2>Connected portfolio</h2><p>{summary?.accounts ?? '—'} client accounts across {summary?.branches ?? '—'} branches</p></div><button className="m-text-button" onClick={() => setActiveNav('accounts')}>Explore accounts <Icon name="arrow" /></button></div><div className="m-regions">{Object.entries(regions).map(([region, value]) => <div key={region}><div><strong>{region}</strong><span>{value.count} accounts</span></div><b>{compact(value.balance)}</b><div className="m-progress"><span style={{ width: `${totalBalance ? value.balance / totalBalance * 100 : 0}%` }} /></div></div>)}</div></section>
        </>}

        {activeNav === 'transactions' && <section className="m-card"><div className="m-card-heading"><div><h2>Transaction ledger <span className="m-count" data-testid="transactions-total">{txsData.total}</span></h2><p>Search and filters query your database directly</p></div><span className="m-chip">25 per page</span></div><form className="m-filters" onSubmit={handleApplyFilters}><label className="m-search"><Icon name="search" /><input data-testid="transaction-search" placeholder="Search ID, reference or client…" value={searchInput} onChange={event => setSearchInput(event.target.value)} /></label><label><span className="m-sr">Status</span><select data-testid="status-filter" value={statusFilter} onChange={event => setStatusFilter(event.target.value)}><option value="all">All statuses</option><option value="settled">Settled</option><option value="pending">Pending</option><option value="exception">Exception</option></select></label><label><span className="m-sr">Region</span><select data-testid="region-filter" value={regionFilter} onChange={event => setRegionFilter(event.target.value)}><option value="all">All regions</option><option>Americas</option><option>EMEA</option><option>APAC</option></select></label><button className="m-button primary" data-testid="apply-filters">Apply filters</button></form>{loadingTxs ? <Loading label="Loading transactions" /> : !txsData.items.length ? <Empty label="No matching transactions" text="Try another client, status or region." /> : transactionTable(txsData.items, true)}<div className="m-pagination"><span>{txsData.total ? `${(page - 1) * 25 + 1}–${Math.min(page * 25, txsData.total)}` : '0'} of {txsData.total} transactions</span><div><button className="m-button" disabled={page === 1 || loadingTxs} onClick={() => setPage(page - 1)}>← Previous</button><span>Page <b data-testid="current-page">{page}</b> of {Math.max(1, Math.ceil(txsData.total / 25))}</span><button className="m-button" data-testid="page-next" disabled={page * 25 >= txsData.total || loadingTxs} onClick={() => setPage(page + 1)}>Next →</button></div></div></section>}
        {activeNav === 'accounts' && <section className="m-card"><div className="m-card-heading"><div><h2>Client portfolio <span className="m-count">{accounts.length}</span></h2><p>{formatUSD(totalBalance)} total account balance</p></div><label className="m-search"><Icon name="search" /><input data-testid="account-search" placeholder="Search clients or branches…" value={accountSearch} onChange={event => setAccountSearch(event.target.value)} /></label></div>{loadingAccounts ? <Loading label="Loading client portfolio" /> : <div className="m-table-scroll"><table className="m-table"><thead><tr><th>Client</th><th>Branch</th><th>Region</th><th className="number">Transactions</th><th className="number"><button className="m-text-button" data-testid="account-sort" onClick={() => setAccountSort(!accountSort)}>Balance {accountSort ? '↓' : '↕'}</button></th></tr></thead><tbody>{filteredAccounts.map(account => <tr data-testid="account-row" key={account.id}><td><div className="m-client"><span className="m-avatar">{initials(account.name)}</span><span><strong>{account.name}</strong><small>Account {String(account.id).padStart(4, '0')}</small></span></div></td><td>{account.branch_name}</td><td>{account.region}</td><td className="number">{account.transaction_count}</td><td className="number m-money">{formatUSD(account.balance)}</td></tr>)}</tbody></table>{!filteredAccounts.length && <Empty label="No matching accounts" text="Try a different client, branch or region." />}</div>}</section>}
        {activeNav === 'exceptions' && <section className="m-card"><div className="m-card-heading"><div><h2>Exception workspace <span className="m-count">{cases.length}</span></h2><p>Ownership, context and a direct path to the underlying transaction</p></div><label className="m-search"><Icon name="search" /><input data-testid="case-search" placeholder="Search cases, owners or clients…" value={caseSearch} onChange={event => setCaseSearch(event.target.value)} /></label></div><div className="m-tabs" role="group" aria-label="Case severity">{['all', 'high', 'medium'].map(value => <button key={value} data-testid={`case-filter-${value}`} aria-pressed={caseFilter === value} className={caseFilter === value ? 'selected' : ''} onClick={() => setCaseFilter(value)}>{value === 'all' ? 'All cases' : value + ' priority'}<span>{value === 'all' ? cases.length : cases.filter(item => item.severity === value).length}</span></button>)}</div>{loadingCases ? <Loading label="Loading exception workspace" /> : <div className="m-table-scroll"><table className="m-table"><thead><tr><th>Case / reason</th><th>Priority</th><th>Client</th><th>Owner</th><th>State</th><th className="number">Exposure</th><th /></tr></thead><tbody>{filteredCases.map(item => <tr data-testid="case-row" key={item.id}><td><button className="m-link" onClick={() => openDetail(item.transaction_id)}>{item.id}</button><small className="m-cell-small">{item.reason}</small></td><td>{badge(item.severity)}</td><td>{item.account_name}</td><td><span className="m-owner"><span className="m-avatar tiny">{initials(item.owner)}</span>{item.owner}</span></td><td>{badge(item.state)}</td><td className="number m-money">{formatUSD(item.amount)}</td><td><button className="m-icon-button" aria-label={`View details ${item.transaction_id}`} onClick={() => openDetail(item.transaction_id)}><Icon name="chevron" /></button></td></tr>)}</tbody></table>{!filteredCases.length && <Empty label="No matching cases" text="Try a different priority or search." />}</div>}</section>}
        <footer className="m-footer"><span>MERIDIAN <b> / </b> OPERATIONS INTELLIGENCE</span><span><Icon name="shield" />Read-only data access · USD</span></footer>
      </main>
    </section>
    {selectedTxId && <div className="m-drawer-backdrop" onClick={event => { if (event.target === event.currentTarget) closeDetail(); }}><div className="m-drawer" data-testid="transaction-detail" ref={drawerRef} role="dialog" aria-modal="true" aria-labelledby="detail-heading" tabIndex={-1}><div className="m-drawer-header"><span className="m-eyebrow">TRANSACTION INTELLIGENCE</span><button className="m-icon-button" aria-label="Close transaction details" data-testid="detail-close" onClick={closeDetail}><Icon name="close" /></button></div><div className="m-drawer-content"><h2 id="detail-heading">The complete picture<span>.</span></h2><p className="m-mono muted">{selectedTxId}</p>{loadingDetail ? <Loading label="Loading transaction details" /> : errorDetail ? <div className="m-error" role="alert">{errorDetail}<button className="m-button" onClick={() => openDetail(selectedTxId)}>Retry</button></div> : detailData && <><div className="m-detail-amount"><span>Transaction amount</span><strong>{formatUSD(detailData.transaction.amount)}</strong>{badge(detailData.transaction.status)}</div><h3>Transaction details</h3><dl className="m-detail-fields">{[['Client', detailData.transaction.account_name], ['Branch', detailData.transaction.branch_name], ['Region', detailData.transaction.region], ['Channel', detailData.transaction.channel], ['Reference', detailData.transaction.reference], ['Booked', date(detailData.transaction.booked_at)]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><h3>Activity timeline <span className="m-count">{detailData.events.length}</span></h3><div className="m-timeline">{detailData.events.map(event => <div key={event.id}><i><Icon name="check" /></i><strong>{event.message}</strong><small>{event.occurred_at.replace('T', ' ').slice(0, 19)} UTC</small></div>)}{!detailData.events.length && <p className="muted">No recorded events.</p>}</div><h3>Associated cases <span className="m-count">{detailData.cases.length}</span></h3>{detailData.cases.map(item => <div className="m-detail-case" key={item.id}><div><span className="m-mono">{item.id}</span>{badge(item.severity)}</div><strong>{item.reason}</strong><small>{item.owner} · {item.state}</small></div>)}{!detailData.cases.length && <p className="muted">No associated cases.</p>}</>}</div><div className="m-drawer-footer"><Icon name="shield" />Data from your connected PostgreSQL source</div></div></div>}
  </div>;
}

function Loading({ label = 'Loading your data' }: { label?: string }) { return <div className="m-loading" role="status"><span />{label}</div>; }
function Empty({ label, text }: { label: string; text: string }) { return <div className="m-empty" data-testid="empty-state"><Icon name="search" /><h3>{label}</h3><p>{text}</p></div>; }
function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    brand: 'M3 19V5l9 8 9-8v14M3 5l9 14 9-14', grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z', arrows: 'M3 7h17m-5-5 5 5-5 5M21 17H4m5-5-5 5 5 5', wallet: 'M3 6a2 2 0 0 1 2-2h14v4M3 6v13a1 1 0 0 0 1 1h16V8H5a2 2 0 0 1-2-2m13 6h4', flag: 'M5 21V3m0 1c6-4 8 4 14 0v9c-6 4-8-4-14 0', shield: 'm12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6zM8 12l3 3 5-6', arrow: 'M5 12h14m-6-6 6 6-6 6', chevron: 'm9 5 7 7-7 7', refresh: 'M20 7V3l-3 3a8 8 0 1 0 3 9M20 7h-5', download: 'M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4', search: 'M21 21l-6-6M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0', check: 'm5 12 4 4L19 6', close: 'm6 6 12 12M6 18 18 6', moon: 'M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11', sun: 'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  };
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.grid} /></svg>;
}

const styles = `
.meridian{--bg:#f5f6f8;--panel:#fff;--text:#1c2937;--muted:#647180;--line:#e6e9ef;--soft:#f7f8fa;--green:#2f7d60;--amber:#976b2d;--red:#b25d52;--accent:#c5eb9f;--navy:#172e32;display:flex;height:100vh;min-height:550px;width:100%;overflow:hidden;background:var(--bg);color:var(--text);font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.5;letter-spacing:-.015em}
.meridian[data-theme=dark]{--bg:#121b22;--panel:#1a262e;--text:#e5edf0;--muted:#8d9ea7;--line:#2b3a43;--soft:#202f38;--navy:#102528;--green:#76bf9c;--amber:#e2b671;--red:#df948b}
.meridian *{box-sizing:border-box}.meridian button,.meridian input,.meridian select{font:inherit}.meridian button{cursor:pointer}.meridian button:disabled{cursor:not-allowed;opacity:.45}.meridian button{transition:background .16s,color .16s,box-shadow .16s}.meridian button:focus-visible,.meridian a:focus-visible,.meridian input:focus-visible,.meridian select:focus-visible{outline:3px solid #77ac98;outline-offset:3px}.meridian h1,.meridian h2,.meridian h3,.meridian p{margin:0}.meridian button:not(.m-button){border:0}.meridian a{color:inherit;text-decoration:none}.m-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}.muted{color:var(--muted)}.number{text-align:right!important}.m-mono{font-family:ui-monospace,SFMono-Regular,monospace}
.m-sidebar{width:224px;background:#15292c;color:#a4b5b6;flex-shrink:0;display:flex;flex-direction:column;padding:29px 15px 0}.m-brand{display:flex;align-items:center;gap:10px;color:#f5fbf8!important;padding:0 9px;font-size:26px;font-weight:650;letter-spacing:-1.2px}.m-brand-symbol{display:flex;align-items:center;justify-content:center;background:var(--accent);color:#1d3831;width:34px;height:36px;border-radius:9px}.m-brand-symbol svg{width:24px;height:24px}.m-brand-period{color:var(--accent)}.m-brand small{display:block;font-size:9px;font-weight:500;letter-spacing:2px;color:#a1b3b2;margin-top:1px}.m-workspace{display:flex;align-items:center;gap:10px;padding:14px 10px;border:1px solid #35484a;border-radius:9px;margin:35px 4px 31px;font-size:12px;color:#e7efea}.m-workspace-icon{width:29px;height:29px;border-radius:6px;display:grid;place-items:center;background:#344744;font-family:Georgia,serif;font-size:18px;color:var(--accent)}.m-workspace small{display:block;font-size:11px;color:#94a9a9}.m-workspace>svg{width:12px;margin-left:auto;color:#819a99}.m-nav-label{font-size:10px;letter-spacing:1.8px;padding:0 14px;margin-bottom:12px;color:#829b9b}.m-nav{width:100%;display:flex;align-items:center;gap:12px;text-align:left;background:transparent;color:#aebebe;border-radius:7px;padding:12px 14px;margin-bottom:5px;font-weight:500}.m-nav:hover{background:#243b3d;color:#fff}.m-nav.active{background:#c5eb9f;color:#213d32}.m-nav b{margin-left:auto;font-size:11px;font-weight:600;background:#ffffff16;border-radius:5px;padding:1px 6px}.m-nav.active b{background:#15372a16}.m-sidebar-note{margin: auto 7px 25px;padding:19px 13px;background:linear-gradient(130deg,#273e3c,#1d3335);border:1px solid #354c49;border-radius:10px}.m-note-icon{display:block;color:var(--accent);margin-bottom:13px}.m-sidebar-note strong{font-size:13px;color:#e9f4ec}.m-sidebar-note p{font-size:11px;line-height:1.8;color:#9db3ae;margin-top:6px}.m-outline-label{display:inline-block;font-size:9px;letter-spacing:1px;padding:5px 7px;border:1px solid #597258;border-radius:4px;color:var(--accent);margin-top:14px}.m-sidebar-bottom{display:flex;align-items:center;gap:9px;border-top:1px solid #35484a;padding:19px 4px;font-size:11px;color:#e0e9e5}.m-sidebar-bottom small{display:block;font-size:9px;color:#8ea5a4}.m-sidebar-bottom .m-online-dot{margin-left:auto}.m-user{width:32px;height:32px;background:#dbe5d5;color:#385546;display:grid;place-items:center;border-radius:50%;font-size:11px;font-weight:650;flex-shrink:0}.m-user.small{width:27px;height:27px;font-size:10px}.m-body{flex:1;min-width:0;display:flex;flex-direction:column}.m-topbar{height:65px;flex-shrink:0;border-bottom:1px solid var(--line);background:var(--panel);padding:0 32px;display:flex;align-items:center;justify-content:space-between}.m-breadcrumb{color:var(--muted);font-size:12px}.m-breadcrumb span{margin:0 16px;color:#b8c2c8}.m-breadcrumb strong{color:var(--text);font-weight:500}.m-top-actions{display:flex;align-items:center;gap:18px}.m-connection{font-size:10px;display:flex;align-items:center;gap:6px;color:var(--muted)}.m-connection i,.m-online-dot{display:inline-block;width:6px;height:6px;flex-shrink:0;border-radius:50%;background:#72b695}.m-connection.offline i{background:var(--red)}.m-main{padding:30px 32px 20px;overflow:auto;min-width:0;flex:1}.m-page-heading{display:flex;justify-content:space-between;align-items:center;gap:20px}.m-eyebrow{font-size:9px;font-weight:650;letter-spacing:1.7px;color:var(--muted)}.m-eyebrow span{margin:0 8px;color:#bec7cc}.m-page-heading h1{font-size:29px;letter-spacing:-1.15px;font-weight:580;margin:7px 0 5px;line-height:1.25}.m-title-dot{color:var(--green)}.m-page-heading p{font-size:12px;color:var(--muted)}.m-heading-actions{display:flex;gap:8px;flex-shrink:0}.m-button{border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--text);display:inline-flex;justify-content:center;align-items:center;gap:7px;padding:9px 12px;font-size:11px!important;font-weight:550!important;white-space:nowrap}.m-button:hover{background:var(--soft)}.m-button svg{width:13px;height:13px}.m-button.primary{background:var(--navy);border-color:var(--navy);color:#f5f8ef}.m-button.primary:hover{background:#2d4c49}.m-context-line{display:flex;gap:14px;align-items:center;color:var(--muted);font-size:10px;margin:15px 0 23px}.m-context-line>span{display:flex;align-items:center;gap:5px}.m-context-line>span+span{border-left:1px solid var(--line);padding-left:14px}.m-kpis{display:grid;grid-template-columns:1.25fr 1fr 1fr 1fr;gap:15px;margin-bottom:23px}.m-kpi{position:relative;overflow:hidden;background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:19px 20px 17px}.m-kpi>div:first-child{display:flex;align-items:center;justify-content:space-between;color:var(--muted);font-size:11px;gap:7px}.m-kpi>div:first-child>svg{width:14px;height:14px}.m-kpi>strong{display:block;font-size:30px;line-height:1.35;letter-spacing:-1.1px;font-weight:580;margin-top:11px}.m-kpi>small{display:block;color:var(--muted);font-size:10px;margin-top:6px;position:relative;z-index:1}.m-kpi.featured{background:var(--navy);color:#fff;border-color:var(--navy)}.m-kpi.featured>div:first-child{color:#bacdc4}.m-kpi.featured>small{color:#bacdc4}.m-spark{position:absolute;right:-6px;bottom:7px;width:106px;height:40px;color:#9fcaa155;pointer-events:none}.m-positive{color:var(--green)}.m-attention-icon{display:grid;place-items:center;width:25px;height:25px;border-radius:6px;background:#d6897315;color:var(--red)}.m-attention-icon svg{width:13px;height:13px}.m-progress{height:4px;border-radius:9px;background:var(--line);overflow:hidden}.m-progress span{display:block;background:var(--green);height:100%;border-radius:9px}.m-kpi .m-progress{position:absolute;bottom:0;left:0;right:0;border-radius:0;height:3px}.m-link{padding:0!important;background:transparent;color:var(--text);font-size:12px!important;font-weight:550!important;text-align:left}.m-link:hover{color:var(--green);text-decoration:underline}.m-kpi .m-link{font-size:10px!important;color:var(--muted)}.m-card{background:var(--panel);border:1px solid var(--line);border-radius:10px;min-width:0;overflow:hidden}.m-card-heading{display:flex;align-items:center;justify-content:space-between;gap:15px;padding:20px 22px 17px}.m-card-heading h2{font-size:14px;font-weight:620;letter-spacing:-.2px;display:flex;align-items:center;gap:8px}.m-card-heading p{font-size:11px;color:var(--muted);margin-top:4px}.m-card-heading>svg{color:var(--muted);width:17px;height:17px}.m-chip{display:flex;align-items:center;gap:6px;font-size:10px;color:var(--muted);white-space:nowrap;border:1px solid var(--line);border-radius:5px;padding:5px 8px}.m-chip i{width:5px;height:5px;background:var(--green);border-radius:50%}.m-chart-grid{display:grid;grid-template-columns:1.8fr 1fr;gap:18px;margin-bottom:20px}.m-chart-stats{display:flex;gap:32px;margin:0 22px 14px;align-items:end}.m-chart-stats>div span{display:block;color:var(--muted);font-size:10px}.m-chart-stats strong{display:block;font-size:22px;font-weight:550;letter-spacing:-.6px;margin-top:3px}.m-chart-count{margin-left:auto;font-size:10px;color:var(--muted);padding-bottom:5px}.m-volume-chart{height:188px;display:flex;padding:0 22px 28px 14px;gap:9px;margin-bottom:10px}.m-y-labels{display:flex;flex-direction:column;justify-content:space-between;font-size:9px;color:var(--muted);width:39px;text-align:right;padding-bottom:1px}.m-plot{flex:1;position:relative;min-width:0}.m-gridlines{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:space-between}.m-gridlines i{border-top:1px dashed var(--line)}.m-bars{height:100%;display:flex;align-items:flex-end;justify-content:space-between;gap:9px;padding:0 5px;position:relative}.m-bars button{flex:1;min-width:0;max-width:30px;border-radius:4px 4px 0 0;background:linear-gradient(0deg,#a8bcaf,#385f55);position:relative;transition:background .15s}.m-bars button:hover,.m-bars button.selected{background:linear-gradient(0deg,#8ebc74,#c5eb9f)}.m-x-labels{position:absolute;top:100%;left:0;right:0;display:flex;justify-content:space-between;font-size:9px;color:var(--muted);padding-top:8px}.m-health{display:flex;align-items:center;gap:24px;justify-content:center;padding:4px 20px 22px;min-height:197px}.m-donut{width:139px;height:139px;flex-shrink:0;border-radius:50%;padding:15px;transform:rotate(-90deg)}.m-donut>div{background:var(--panel);border-radius:50%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;transform:rotate(90deg)}.m-donut strong{font-size:27px;font-weight:550;letter-spacing:-1px}.m-donut small{font-size:6px;letter-spacing:.8px;color:var(--muted);margin-top:2px}.m-health-legend{flex:1;max-width:135px}.m-health-legend>div{display:flex;justify-content:space-between;align-items:center;font-size:11px;padding:9px 0;border-bottom:1px solid var(--line);gap:14px}.m-health-legend>div:last-child{border:0}.m-health-legend span{display:flex;align-items:center;gap:7px;color:var(--muted)}.m-health-legend i{width:6px;height:6px;border-radius:50%;display:inline-block}.m-health-legend strong{font-size:12px;font-weight:550}.m-health-note{display:flex;align-items:center;gap:11px;background:var(--soft);border-top:1px solid var(--line);padding:15px 22px;font-size:11px;color:var(--muted)}.m-health-note>svg{color:var(--amber);width:17px}.m-health-note button{display:block;background:none;padding:2px 0 0;color:var(--text);font-size:11px;font-weight:550}.m-health-note button span{margin-left:7px}.m-lower-grid{display:grid;grid-template-columns:1.8fr 1fr;gap:18px;margin-bottom:20px}.m-text-button{display:inline-flex;align-items:center;gap:6px;background:transparent;color:var(--text);padding:0;font-size:11px;white-space:nowrap}.m-text-button svg{width:12px;height:12px}.m-text-button:hover{color:var(--green)}.m-table-scroll{position:relative;overflow-x:auto}.m-table{width:100%;border-collapse:collapse;text-align:left;white-space:nowrap;font-size:12px}.m-table thead{background:var(--soft)}.m-table th{font-size:9px;letter-spacing:.5px;color:var(--muted);font-weight:550;text-transform:uppercase;padding:11px 18px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.m-table td{padding:13px 18px;border-bottom:1px solid var(--line)}.m-table tr:last-child td{border-bottom:0}.m-table tbody tr:hover{background:var(--soft)}.m-client{display:flex;align-items:center;gap:10px}.m-client small,.m-cell-small{display:block;color:var(--muted);font-size:10px;margin-top:3px}.m-client strong{font-weight:550}.m-avatar{width:30px;height:30px;display:grid;place-items:center;border-radius:7px;background:#e7ede6;color:#5b7661;font-size:10px;font-weight:550;flex-shrink:0}.m-avatar.tiny{width:23px;height:23px;font-size:9px;border-radius:50%}.m-owner{display:flex;gap:7px;align-items:center}.m-money{font-variant-numeric:tabular-nums;font-weight:550}.m-badge{display:inline-flex;align-items:center;gap:5px;padding:4px 7px;line-height:1.2;border-radius:4px;background:var(--soft);font-size:9px;color:var(--muted);text-transform:capitalize;white-space:nowrap}.m-badge i{width:4px;height:4px;border-radius:50%;background:currentColor}.m-badge.settled,.m-badge.open{color:var(--green);background:#4d9b7210}.m-badge.pending,.m-badge.medium{color:var(--amber);background:#d8a24a12}.m-badge.exception,.m-badge.high{color:var(--red);background:#d7837312}.m-icon-button{padding:6px;display:inline-flex;align-items:center;justify-content:center;background:transparent;color:var(--muted);border-radius:5px}.m-icon-button:hover{background:var(--soft);color:var(--text)}.m-icon-button svg{width:16px;height:16px}.m-count{display:inline-flex;align-items:center;justify-content:center;background:var(--soft);color:var(--muted);padding:2px 7px;border-radius:5px;font-size:10px;font-weight:500}.m-queue{padding:0 20px}.m-queue>button{width:100%;display:flex;align-items:center;gap:10px;text-align:left;padding:16px 0;border-bottom:1px solid var(--line);background:transparent;color:var(--text)}.m-queue>button:last-child{border:0}.m-queue>button:hover strong{color:var(--green)}.m-queue-mark{width:30px;height:33px;flex-shrink:0;display:grid;place-items:center;border-radius:6px;background:#d8a24a12;color:var(--amber)}.m-queue-mark.high{color:var(--red);background:#d7837312}.m-queue-mark svg{width:14px;height:14px}.m-queue>button>span:nth-child(2){flex:1;min-width:0}.m-queue strong{font-weight:550;font-size:11px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.m-queue small{font-size:9px;color:var(--muted);display:block;margin-top:5px}.m-queue>button>span:last-child{text-align:right}.m-card-footer{width:100%;padding:14px 21px!important;font-size:11px!important;background:var(--soft);border-top:1px solid var(--line)!important;color:var(--text);display:flex;align-items:center;justify-content:space-between}.m-card-footer svg{width:12px;height:12px}.m-regions{display:grid;grid-template-columns:repeat(3,1fr);gap:35px;padding:0 22px 22px}.m-regions>div>div:first-child{display:flex;align-items:center;justify-content:space-between;gap:5px}.m-regions strong{font-size:11px;font-weight:550}.m-regions span{font-size:9px;color:var(--muted)}.m-regions b{display:block;font-size:19px;font-weight:550;letter-spacing:-.4px;margin:5px 0 11px}.m-filters{position:relative;display:flex;gap:9px;padding:0 22px 20px;flex-wrap:wrap}.m-search{display:flex;align-items:center;gap:8px;min-width:160px;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px 10px;color:var(--muted)}.m-filters .m-search{flex:1}.m-search svg{width:14px;height:14px;flex-shrink:0}.m-search input{border:0;outline:none;background:transparent;color:var(--text);font-size:11px;min-width:0;width:100%}.m-search input::placeholder{color:var(--muted)}.meridian select{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:6px;font-size:11px;padding:9px;min-width:119px}.m-pagination{padding:16px 22px;border-top:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:15px;color:var(--muted);font-size:11px}.m-pagination>div{display:flex;gap:15px;align-items:center}.m-pagination b{color:var(--text);font-weight:550}.m-tabs{display:flex;gap:20px;border-bottom:1px solid var(--line);padding:0 22px}.m-tabs button{padding:0 0 13px;background:none;color:var(--muted);font-size:11px;text-transform:capitalize;border-bottom:2px solid transparent!important;display:flex;align-items:center;gap:8px}.m-tabs button.selected{color:var(--text);border-bottom-color:var(--green)!important}.m-tabs span{background:var(--soft);border-radius:4px;padding:1px 5px;font-size:9px}.m-footer{display:flex;justify-content:space-between;gap:15px;font-size:9px;letter-spacing:.7px;color:var(--muted);padding-top:26px}.m-footer>span:last-child{display:flex;align-items:center;gap:5px;letter-spacing:0}.m-footer svg{width:11px;height:11px}.m-footer b{font-weight:400;color:#c2cbd1;margin:0 5px}.m-error{display:flex;align-items:center;gap:12px;border:1px solid #ce897544;background:#ce897512;color:var(--text);border-radius:8px;padding:14px 16px;margin-bottom:20px}.m-error>span{flex:1;min-width:0}.m-error strong{display:block;font-weight:550;font-size:12px}.m-error small{display:block;font-size:11px;color:var(--muted);overflow-wrap:anywhere;margin-top:2px}.m-error>svg{color:var(--red);flex-shrink:0}.m-loading{display:flex;align-items:center;justify-content:center;gap:10px;padding:60px 20px;color:var(--muted);font-size:12px;min-height:160px}.m-loading>span{width:15px;height:15px;border:2px solid var(--line);border-top-color:var(--green);border-radius:50%;animation:m-spin .8s linear infinite}.m-empty{padding:65px 25px;text-align:center;color:var(--muted)}.m-empty>svg{width:25px;height:25px;margin:auto auto 12px}.m-empty h3{font-size:14px;color:var(--text);font-weight:550}.m-empty p{font-size:12px;margin-top:6px}
.m-drawer-backdrop{position:fixed;inset:0;z-index:100;background:#0b1b2b70;backdrop-filter:blur(3px);display:flex;justify-content:flex-end}.m-drawer{width:500px;max-width:100%;height:100%;background:var(--panel);border-left:1px solid var(--line);box-shadow:-20px 0 100px #07192230;display:flex;flex-direction:column;outline:none;animation:m-slide .2s ease}.m-drawer-header{padding:23px 28px 12px;display:flex;justify-content:space-between;align-items:center}.m-drawer-content{padding:5px 30px 35px;overflow:auto;flex:1}.m-drawer-content>h2{font-size:26px;font-weight:550;letter-spacing:-.8px;margin-bottom:8px}.m-drawer-content>h2>span{color:var(--green)}.m-drawer-content>p{font-size:12px}.m-detail-amount{border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:23px 0;margin:24px 0}.m-detail-amount>span:first-child{font-size:11px;color:var(--muted);display:block}.m-detail-amount strong{display:block;font-size:37px;font-weight:500;letter-spacing:-1.2px;margin:5px 0 9px}.m-drawer h3{font-size:13px;font-weight:600;margin:25px 0 16px;display:flex;align-items:center;gap:8px}.m-detail-fields{margin:0;display:grid;gap:15px}.m-detail-fields>div{display:flex;justify-content:space-between;gap:20px}.m-detail-fields dt{font-size:12px;color:var(--muted)}.m-detail-fields dd{margin:0;font-size:12px;font-weight:500;text-align:right}.m-timeline{padding-left:7px}.m-timeline>div{position:relative;padding:0 0 24px 29px;border-left:1px solid var(--line)}.m-timeline>div:last-child{border-color:transparent;padding-bottom:0}.m-timeline i{position:absolute;left:-10px;top:0;width:19px;height:19px;border-radius:50%;display:grid;place-items:center;background:var(--soft);color:var(--green);border:3px solid var(--panel)}.m-timeline i svg{width:11px;height:11px}.m-timeline strong{display:block;font-size:12px;font-weight:500}.m-timeline small{display:block;font-size:10px;color:var(--muted);margin-top:5px}.m-detail-case{border:1px solid var(--line);border-radius:8px;padding:15px;margin-bottom:10px;background:var(--soft)}.m-detail-case>div{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:11px}.m-detail-case>strong{display:block;font-size:13px;font-weight:550;margin-top:10px}.m-detail-case>small{display:block;font-size:11px;color:var(--muted);margin-top:5px}.m-drawer-footer{display:flex;align-items:center;gap:7px;padding:17px 30px;font-size:10px;color:var(--muted);border-top:1px solid var(--line)}.m-drawer-footer svg{width:13px;height:13px}
@keyframes m-spin{to{transform:rotate(360deg)}}@keyframes m-slide{from{transform:translateX(30px);opacity:0}to{transform:translateX(0);opacity:1}}
@media(min-width:1600px){.m-main{padding:38px 45px}.m-page-heading h1{font-size:34px}.m-volume-chart{height:240px}.m-card-heading{padding-top:23px;padding-bottom:20px}.m-health{min-height:246px}.m-donut{width:165px;height:165px}.m-kpi{padding:23px}.m-kpi>strong{font-size:36px}}
@media(max-width:1250px){.m-sidebar{width:190px;padding-left:10px;padding-right:10px}.m-main{padding:25px 23px}.m-topbar{padding:0 23px}.m-page-heading h1{font-size:25px}.m-kpi{padding:16px}.m-kpi>strong{font-size:26px}.m-health{gap:14px;padding-left:14px;padding-right:14px}.m-donut{width:110px;height:110px;padding:12px}.m-donut strong{font-size:22px}.m-chart-grid,.m-lower-grid{grid-template-columns:1.6fr 1fr}.m-table td,.m-table th{padding-left:13px;padding-right:13px}.m-queue{padding:0 14px}.m-queue small{font-size:7px}.m-chart-count{display:none}.m-brand{font-size:23px}.m-heading-actions .m-button{padding:8px}.m-page-heading{gap:12px}}
@media(max-width:1000px){.m-sidebar{width:70px;align-items:center;padding:24px 9px 0}.m-brand{padding:0}.m-brand>span:last-child,.m-workspace,.m-nav-label,.m-nav>span,.m-nav b,.m-sidebar-note,.m-sidebar-bottom>span:not(:first-child){display:none}.m-sidebar nav{width:100%;margin-top:45px}.m-nav{padding:13px;justify-content:center}.m-sidebar-bottom{margin-top:auto;border-top:0;padding:20px 0}.m-topbar{height:55px}.m-card-heading{padding:17px}.m-kpi>div:first-child>svg,.m-attention-icon{display:none}.m-kpi>strong{font-size:25px}.m-health{min-height:200px}.m-card-heading h2{font-size:13px}.m-kpi>small{font-size:9px}.m-queue-mark{display:none}.m-chart-stats{margin-left:17px;gap:20px}}
@media(max-width:760px){.m-main{padding:22px 17px}.m-topbar{padding:0 17px}.m-page-heading{align-items:flex-start;flex-direction:column;gap:14px}.m-page-heading h1{font-size:25px}.m-heading-actions{align-self:flex-end}.m-kpis{grid-template-columns:repeat(2,1fr);gap:10px}.m-chart-grid,.m-lower-grid{grid-template-columns:1fr}.m-health{min-height:170px}.m-donut{width:130px;height:130px}.m-card-heading .m-search{min-width:0;width:180px}.m-card-heading{flex-wrap:wrap}.m-health-legend{max-width:180px}.m-chip{font-size:9px}.m-connection{display:none}.m-regions{gap:20px}.m-pagination{flex-direction:column;align-items:flex-start}.m-pagination>div{gap:12px}.m-footer{font-size:7px}.m-footer>span:last-child{display:none}.m-context-line{font-size:9px;gap:8px}.m-context-line>span+span{padding-left:8px}.m-queue{padding:0 18px}.m-queue-mark{display:grid}.m-queue small{font-size:10px}.m-queue strong{font-size:12px}}
@media(max-width:480px){.m-sidebar{width:53px;padding-left:6px;padding-right:6px}.m-brand-symbol{width:30px;height:32px}.m-nav{padding:12px 9px}.m-nav svg{width:16px}.m-main{padding:20px 12px}.m-page-heading h1{font-size:23px}.m-page-heading p{font-size:11px}.m-topbar{padding:0 12px}.m-top-actions{gap:8px}.m-breadcrumb{font-size:11px}.m-breadcrumb span{margin:0 7px}.m-kpi{padding:14px}.m-kpi>div:first-child{font-size:10px}.m-kpi>strong{font-size:25px}.m-kpi>small{font-size:9px}.m-context-line>span+span{display:none}.m-regions{grid-template-columns:1fr;gap:17px}.m-regions>div>div:first-child{justify-content:flex-start;gap:10px}.m-regions b{font-size:17px}.m-filters{padding:0 15px 17px}.meridian select{min-width:95px}.m-card-heading{padding:17px 15px}.m-card-heading .m-search{width:100%}.m-chart-stats{margin-left:15px}.m-chip{padding:4px;font-size:7px}.m-volume-chart{padding-right:14px}.m-bars{gap:5px}.m-pagination{padding:14px}.m-pagination>div{gap:9px;font-size:10px}.m-pagination .m-button{padding:7px 8px}.m-drawer-content{padding:5px 23px 30px}.m-drawer-content>h2{font-size:24px}.m-drawer-header{padding-left:23px;padding-right:23px}.m-queue>button>span:last-child{display:none}}
@media(prefers-reduced-motion:reduce){.meridian *{animation:none!important;transition:none!important}}
`;
