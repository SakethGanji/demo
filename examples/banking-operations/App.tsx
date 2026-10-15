/** Meridian Operations App
 * Comprehensive banking operations dashboard with real-time summary, transaction auditing,
 * account monitoring, and exception case tracking using typed API client. */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { api, OpsSummaryResponse, OpsTrendsResponse, OpsTransactionsResponse, OpsAccountsResponse, OpsCasesResponse, OpsTransactionResponse } from './api/client';

/** Format numbers as USD currency strings */
const formatUSD = (amount?: number): string =>
  typeof amount === 'number' ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount) : '$0.00';

/** Primary entry component for Meridian Operations */
export default function App() {
  const [activeNav, setActiveNav] = useState<'overview' | 'transactions' | 'accounts' | 'exceptions'>('overview');

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
    setSelectedTxId(id);
    setLoadingDetail(true);
    setErrorDetail(null);
    try {
      const res = await api.opsTransaction({ id });
      setDetailData(res);
    } catch (err: any) {
      setErrorDetail(err.message || 'Failed to load transaction details');
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  // Keyboard escape handler for detail drawer
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selectedTxId) {
        setSelectedTxId(null);
        setDetailData(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedTxId]);

  // Initial load & Nav change triggers
  useEffect(() => {
    fetchSummary();
  }, [fetchSummary]);

  useEffect(() => {
    if (activeNav === 'overview') fetchOverview();
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
    if (activeNav === 'overview') fetchOverview();
    if (activeNav === 'transactions') fetchTransactions(page, appliedFilters);
    if (activeNav === 'accounts') fetchAccounts();
    if (activeNav === 'exceptions') fetchCases();
  };

  // Max trend volume calculation for bar graph scaling
  const maxVolume = trends && trends.length > 0 ? Math.max(...trends.map(t => t.volume || 0), 1) : 1;

  return (
    <div className="h-full flex flex-col bg-slate-900 text-slate-100 font-sans antialiased">
      {/* Top Navigation Bar */}
      <header className="h-14 border-b border-slate-800 bg-slate-950 px-4 flex items-center justify-between shrink-0">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center font-bold text-white shadow-md shadow-indigo-500/20">
            M
          </div>
          <div>
            <h1 className="font-semibold text-base leading-tight tracking-wide text-slate-100">Meridian Operations</h1>
            <p className="text-xs text-slate-400">Synthetic data / USD</p>
          </div>
        </div>

        <button
          data-testid="app-refresh"
          onClick={handleRefresh}
          disabled={loadingSummary}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-200 border border-slate-700 transition"
        >
          <svg className={`w-3.5 h-3.5 ${loadingSummary ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          {loadingSummary ? 'Refreshing...' : 'Refresh'}
        </button>
      </header>

      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Sidebar */}
        <aside className="w-56 border-r border-slate-800 bg-slate-950 p-3 flex flex-col gap-1 shrink-0">
          {[
            { id: 'overview', label: 'Overview', testid: 'nav-overview', icon: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6' },
            { id: 'transactions', label: 'Transactions', testid: 'nav-transactions', icon: 'M9 14l6-6m-5.5.5h.01m4.99 5h.01M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16l4-2 4 2 4-2 4 2z' },
            { id: 'accounts', label: 'Accounts', testid: 'nav-accounts', icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z' },
            { id: 'exceptions', label: 'Exceptions', testid: 'nav-exceptions', icon: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z' },
          ].map(item => (
            <button
              key={item.id}
              data-testid={item.testid}
              onClick={() => setActiveNav(item.id as any)}
              className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition ${
                activeNav === item.id ? 'bg-indigo-600/20 text-indigo-400 border border-indigo-500/30' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'
              }`}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={item.icon} />
              </svg>
              {item.label}
            </button>
          ))}
        </aside>

        {/* Main Content Body */}
        <main className="flex-1 min-h-0 overflow-y-auto p-6 flex flex-col gap-6">
          {/* Error Banner */}
          {(errorSummary || (activeNav === 'overview' && errorOverview)) && (
            <div className="bg-rose-500/10 border border-rose-500/30 rounded-xl p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-rose-200">
              <div className="flex items-center gap-2.5">
                <svg className="w-5 h-5 text-rose-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span className="text-sm font-medium">
                  {errorSummary || (activeNav === 'overview' && errorOverview)}
                </span>
              </div>
              <button
                data-testid="app-retry"
                onClick={handleRefresh}
                className="px-3.5 py-1.5 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-xs font-semibold shadow transition flex items-center gap-1 shrink-0"
              >
                Retry
              </button>
            </div>
          )}

          {/* Summary KPI Banner */}
          <div className="relative">
            {loadingSummary && (
              <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-[1px] rounded-xl z-10 flex items-center justify-center">
                <span className="text-xs font-medium text-indigo-300 bg-slate-900/90 px-3 py-1 rounded-full border border-indigo-500/30 flex items-center gap-2">
                  <svg className="w-3.5 h-3.5 animate-spin text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  Updating summary...
                </span>
              </div>
            )}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <div className="bg-slate-800/80 border border-slate-700/60 p-3.5 rounded-xl">
              <span className="text-xs font-medium text-slate-400 block mb-1">Volume</span>
              <span className="text-lg font-bold text-slate-100">{formatUSD(summary?.volume)}</span>
            </div>
            <div className="bg-slate-800/80 border border-slate-700/60 p-3.5 rounded-xl">
              <span className="text-xs font-medium text-slate-400 block mb-1">Transactions</span>
              <span data-testid="kpi-transactions" className="text-lg font-bold text-slate-100">
                {summary?.transactions ?? 0}
              </span>
            </div>
            <div className="bg-slate-800/80 border border-slate-700/60 p-3.5 rounded-xl">
              <span className="text-xs font-medium text-slate-400 block mb-1">Pending</span>
              <span className="text-lg font-bold text-amber-400">{summary?.pending ?? 0}</span>
            </div>
            <div className="bg-slate-800/80 border border-slate-700/60 p-3.5 rounded-xl">
              <span className="text-xs font-medium text-slate-400 block mb-1">Exceptions</span>
              <span data-testid="kpi-exceptions" className="text-lg font-bold text-rose-400">
                {summary?.exceptions ?? 0}
              </span>
            </div>
            <div className="bg-slate-800/80 border border-slate-700/60 p-3.5 rounded-xl">
              <span className="text-xs font-medium text-slate-400 block mb-1">Accounts</span>
              <span className="text-lg font-bold text-slate-100">{summary?.accounts ?? 0}</span>
            </div>
            <div className="bg-slate-800/80 border border-slate-700/60 p-3.5 rounded-xl">
              <span className="text-xs font-medium text-slate-400 block mb-1">Branches</span>
              <span className="text-lg font-bold text-slate-100">{summary?.branches ?? 0}</span>
            </div>
          </div>
        </div>

          {/* VIEW: OVERVIEW */}
          {activeNav === 'overview' && (
            <div className="flex flex-col gap-6">
              {/* Daily Volume Bar Chart */}
              <div className="bg-slate-800/50 border border-slate-700/60 rounded-xl p-5">
                <h2 className="text-sm font-semibold text-slate-200 mb-4">15-Day Daily Volume Trend</h2>
                {loadingOverview ? (
                  <div className="h-40 flex items-center justify-center text-slate-400 text-sm">Loading trend data...</div>
                ) : errorOverview ? (
                  <div className="h-40 flex items-center justify-center text-rose-400 text-sm">{errorOverview}</div>
                ) : (
                  <div data-testid="volume-chart" className="h-44 flex items-end justify-between gap-2 pt-6 pb-2 px-2 border-b border-slate-700/50">
                    {trends.slice(0, 15).map((item, idx) => {
                      const pct = Math.max(8, Math.round(((item.volume || 0) / maxVolume) * 100));
                      const titleVal = `${item.day}/${item.volume}`;
                      return (
                        <div key={idx} className="flex-1 flex flex-col items-center h-full justify-end group relative">
                          <div className="flex-1 min-h-0 w-full flex items-end justify-center">
                          <div
                            data-testid="trend-bar"
                            title={titleVal}
                            style={{ height: `${pct}%` }}
                            className="shrink-0 w-full max-w-[24px] bg-indigo-500/80 hover:bg-indigo-400 rounded-t transition-all cursor-pointer"
                          />
                          </div>
                          <span className="text-[10px] text-slate-400 mt-2 truncate w-full text-center">
                            {item.day ? item.day.slice(5) : ''}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Two Column Grid for Recent Activity & Cases */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Recent Transactions */}
                <div className="bg-slate-800/50 border border-slate-700/60 rounded-xl p-5">
                  <div className="flex justify-between items-center mb-4">
                    <h2 className="text-sm font-semibold text-slate-200">Recent Transactions</h2>
                    <button onClick={() => setActiveNav('transactions')} className="text-xs text-indigo-400 hover:underline font-medium">View All</button>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs text-slate-300">
                      <thead className="bg-slate-900/60 text-slate-400 border-b border-slate-700/60">
                        <tr>
                          <th className="p-2">ID</th>
                          <th className="p-2">Account</th>
                          <th className="p-2">Amount</th>
                          <th className="p-2">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800">
                        {overviewTxs.map((tx) => (
                          <tr key={tx.id} onClick={() => tx.id && openDetail(tx.id)} className="hover:bg-slate-800/80 cursor-pointer">
                            <td className="p-2 font-mono text-indigo-300">{tx.id}</td>
                            <td className="p-2 truncate max-w-[120px]">{tx.account_name}</td>
                            <td className="p-2 font-medium">{formatUSD(tx.amount)}</td>
                            <td className="p-2">
                              <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${
                                tx.status === 'settled' ? 'bg-emerald-500/20 text-emerald-400' :
                                tx.status === 'pending' ? 'bg-amber-500/20 text-amber-400' : 'bg-rose-500/20 text-rose-400'
                              }`}>
                                {tx.status}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Open Exceptions Queue */}
                <div className="bg-slate-800/50 border border-slate-700/60 rounded-xl p-5">
                  <div className="flex justify-between items-center mb-4">
                    <h2 className="text-sm font-semibold text-slate-200">Exception Queue</h2>
                    <button onClick={() => setActiveNav('exceptions')} className="text-xs text-indigo-400 hover:underline font-medium">View All</button>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs text-slate-300">
                      <thead className="bg-slate-900/60 text-slate-400 border-b border-slate-700/60">
                        <tr>
                          <th className="p-2">Case ID</th>
                          <th className="p-2">Reason</th>
                          <th className="p-2">Severity</th>
                          <th className="p-2">Amount</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800">
                        {overviewCases.map((c) => (
                          <tr key={c.id} onClick={() => c.transaction_id && openDetail(c.transaction_id)} className="hover:bg-slate-800/80 cursor-pointer">
                            <td className="p-2 font-mono text-slate-300">{c.id}</td>
                            <td className="p-2 truncate max-w-[140px]">{c.reason}</td>
                            <td className="p-2">
                              <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                                c.severity === 'high' ? 'bg-rose-500/20 text-rose-400' : 'bg-amber-500/20 text-amber-400'
                              }`}>
                                {c.severity}
                              </span>
                            </td>
                            <td className="p-2 font-medium">{formatUSD(c.amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* VIEW: TRANSACTIONS */}
          {activeNav === 'transactions' && (
            <div className="flex flex-col gap-4">
              {/* Filter Controls Form */}
              <form onSubmit={handleApplyFilters} className="bg-slate-800/50 border border-slate-700/60 p-4 rounded-xl flex flex-wrap items-center gap-3">
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] text-slate-400 font-medium">Status</label>
                  <select
                    data-testid="status-filter"
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value)}
                    className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
                  >
                    <option value="all">all</option>
                    <option value="settled">settled</option>
                    <option value="pending">pending</option>
                    <option value="exception">exception</option>
                  </select>
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-[11px] text-slate-400 font-medium">Region</label>
                  <select
                    data-testid="region-filter"
                    value={regionFilter}
                    onChange={(e) => setRegionFilter(e.target.value)}
                    className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
                  >
                    <option value="all">all</option>
                    <option value="Americas">Americas</option>
                    <option value="EMEA">EMEA</option>
                    <option value="APAC">APAC</option>
                  </select>
                </div>

                <div className="flex flex-col gap-1 flex-1 min-w-[200px]">
                  <label className="text-[11px] text-slate-400 font-medium">Search</label>
                  <input
                    type="text"
                    data-testid="transaction-search"
                    placeholder="Search reference, account..."
                    value={searchInput}
                    onChange={(e) => setSearchInput(e.target.value)}
                    className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
                  />
                </div>

                <div className="flex items-end self-end">
                  <button
                    type="submit"
                    data-testid="apply-filters"
                    className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-md transition"
                  >
                    Apply Filters
                  </button>
                </div>
              </form>

              {/* Transactions Table */}
              <div className="bg-slate-800/50 border border-slate-700/60 rounded-xl overflow-hidden flex flex-col">
                {loadingTxs ? (
                  <div className="p-12 text-center text-slate-400 text-sm">Loading transactions...</div>
                ) : errorTxs ? (
                  <div className="p-12 text-center text-rose-400 text-sm flex flex-col gap-2 items-center">
                    <span>{errorTxs}</span>
                    <button onClick={() => fetchTransactions(page, appliedFilters)} className="px-3 py-1 bg-slate-800 rounded text-xs text-slate-200 hover:bg-slate-700 border border-slate-600">
                      Retry
                    </button>
                  </div>
                ) : !txsData.items || txsData.items.length === 0 ? (
                  <div data-testid="empty-state" className="p-12 text-center text-slate-400 text-sm">
                    No transactions found matching criteria.
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs text-slate-300">
                      <thead className="bg-slate-900/80 text-slate-400 border-b border-slate-700/60 uppercase text-[10px] tracking-wider">
                        <tr>
                          <th className="p-3">ID</th>
                          <th className="p-3">Booked At</th>
                          <th className="p-3">Account Name</th>
                          <th className="p-3">Branch</th>
                          <th className="p-3">Region</th>
                          <th className="p-3">Channel</th>
                          <th className="p-3">Amount</th>
                          <th className="p-3">Status</th>
                          <th className="p-3 text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800">
                        {txsData.items.map((tx) => (
                          <tr
                            key={tx.id}
                            data-testid="transaction-row"
                            data-id={tx.id}
                            className="hover:bg-slate-800/80 transition cursor-pointer"
                            onClick={() => tx.id && openDetail(tx.id)}
                          >
                            <td className="p-3 font-mono text-indigo-300 font-medium">{tx.id}</td>
                            <td className="p-3 text-slate-400">{tx.booked_at ? new Date(tx.booked_at).toLocaleDateString() : '-'}</td>
                            <td className="p-3 font-medium text-slate-200">{tx.account_name}</td>
                            <td className="p-3 text-slate-400">{tx.branch_name}</td>
                            <td className="p-3 text-slate-400">{tx.region}</td>
                            <td className="p-3 text-slate-400 capitalize">{tx.channel}</td>
                            <td className="p-3 font-semibold text-slate-100">{formatUSD(tx.amount)}</td>
                            <td className="p-3">
                              <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                                tx.status === 'settled' ? 'bg-emerald-500/20 text-emerald-400' :
                                tx.status === 'pending' ? 'bg-amber-500/20 text-amber-400' : 'bg-rose-500/20 text-rose-400'
                              }`}>
                                {tx.status}
                              </span>
                            </td>
                            <td className="p-3 text-right">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (tx.id) openDetail(tx.id);
                                }}
                                className="px-2.5 py-1 text-indigo-400 hover:text-indigo-300 hover:bg-indigo-500/10 rounded transition font-medium"
                              >
                                Details
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Pagination Controls */}
                <div className="p-3 border-t border-slate-700/60 bg-slate-900/50 flex items-center justify-between text-xs text-slate-400">
                  <div>
                    Total: <span data-testid="transactions-total">{txsData.total ?? 0}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <button
                      data-testid="page-prev"
                      disabled={page <= 1}
                      onClick={() => setPage(p => Math.max(1, p - 1))}
                      className="px-3 py-1 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:hover:bg-slate-800 text-slate-200 transition"
                    >
                      Prev
                    </button>
                    <span>
                      Page <span data-testid="current-page">{page}</span>
                    </span>
                    <button
                      data-testid="page-next"
                      disabled={!txsData.total || page >= Math.ceil(txsData.total / (txsData.page_size || 25))}
                      onClick={() => setPage(p => p + 1)}
                      className="px-3 py-1 rounded bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:hover:bg-slate-800 text-slate-200 transition"
                    >
                      Next
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* VIEW: ACCOUNTS */}
          {activeNav === 'accounts' && (
            <div className="bg-slate-800/50 border border-slate-700/60 rounded-xl overflow-hidden">
              {loadingAccounts ? (
                <div className="p-12 text-center text-slate-400 text-sm">Loading accounts...</div>
              ) : errorAccounts ? (
                <div className="p-12 text-center text-rose-400 text-sm flex flex-col gap-2 items-center">
                  <span>{errorAccounts}</span>
                  <button onClick={fetchAccounts} className="px-3 py-1 bg-slate-800 rounded text-xs text-slate-200 hover:bg-slate-700 border border-slate-600">
                    Retry
                  </button>
                </div>
              ) : !accounts || accounts.length === 0 ? (
                <div data-testid="empty-state" className="p-12 text-center text-slate-400 text-sm">
                  No accounts found.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs text-slate-300">
                    <thead className="bg-slate-900/80 text-slate-400 border-b border-slate-700/60 uppercase text-[10px] tracking-wider">
                      <tr>
                        <th className="p-3">Account ID</th>
                        <th className="p-3">Client / Name</th>
                        <th className="p-3">Branch</th>
                        <th className="p-3">Region</th>
                        <th className="p-3">Balance</th>
                        <th className="p-3">Tx Count</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800">
                      {accounts.map((acc) => (
                        <tr key={acc.id} data-testid="account-row" className="hover:bg-slate-800/80 transition">
                          <td className="p-3 font-mono text-indigo-300 font-medium">{acc.id}</td>
                          <td className="p-3 font-semibold text-slate-100">{acc.name}</td>
                          <td className="p-3 text-slate-400">{acc.branch_name}</td>
                          <td className="p-3 text-slate-400">{acc.region}</td>
                          <td className="p-3 font-semibold text-emerald-400">{formatUSD(acc.balance)}</td>
                          <td className="p-3 text-slate-300">{acc.transaction_count ?? 0}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* VIEW: EXCEPTIONS */}
          {activeNav === 'exceptions' && (
            <div className="bg-slate-800/50 border border-slate-700/60 rounded-xl overflow-hidden">
              {loadingCases ? (
                <div className="p-12 text-center text-slate-400 text-sm">Loading exceptions...</div>
              ) : errorCases ? (
                <div className="p-12 text-center text-rose-400 text-sm flex flex-col gap-2 items-center">
                  <span>{errorCases}</span>
                  <button onClick={fetchCases} className="px-3 py-1 bg-slate-800 rounded text-xs text-slate-200 hover:bg-slate-700 border border-slate-600">
                    Retry
                  </button>
                </div>
              ) : !cases || cases.length === 0 ? (
                <div data-testid="empty-state" className="p-12 text-center text-slate-400 text-sm">
                  No exception cases found.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs text-slate-300">
                    <thead className="bg-slate-900/80 text-slate-400 border-b border-slate-700/60 uppercase text-[10px] tracking-wider">
                      <tr>
                        <th className="p-3">Case ID</th>
                        <th className="p-3">Severity</th>
                        <th className="p-3">Owner</th>
                        <th className="p-3">State</th>
                        <th className="p-3">Reason</th>
                        <th className="p-3">Account Name</th>
                        <th className="p-3">Amount</th>
                        <th className="p-3 text-right">Transaction</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800">
                      {cases.map((c) => (
                        <tr key={c.id} data-testid="case-row" className="hover:bg-slate-800/80 transition">
                          <td className="p-3 font-mono text-indigo-300 font-medium">{c.id}</td>
                          <td className="p-3">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                              c.severity === 'high' ? 'bg-rose-500/20 text-rose-400' : 'bg-amber-500/20 text-amber-400'
                            }`}>
                              {c.severity}
                            </span>
                          </td>
                          <td className="p-3 text-slate-300">{c.owner}</td>
                          <td className="p-3 text-slate-400 uppercase text-[10px] font-semibold">{c.state}</td>
                          <td className="p-3 text-slate-200">{c.reason}</td>
                          <td className="p-3 text-slate-300">{c.account_name}</td>
                          <td className="p-3 font-medium text-slate-100">{formatUSD(c.amount)}</td>
                          <td className="p-3 text-right">
                            {c.transaction_id && (
                              <button
                                onClick={() => openDetail(c.transaction_id!)}
                                className="px-2.5 py-1 text-indigo-400 hover:text-indigo-300 hover:bg-indigo-500/10 rounded transition font-medium"
                              >
                                View {c.transaction_id}
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </main>
      </div>

      {/* Slide-over Transaction Detail Modal */}
      {selectedTxId && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex justify-end">
          <div
            data-testid="transaction-detail"
            className="w-full max-w-lg bg-slate-900 border-l border-slate-800 h-full flex flex-col p-6 overflow-y-auto shadow-2xl"
          >
            <div className="flex items-center justify-between pb-4 border-b border-slate-800 mb-6">
              <div>
                <h3 className="text-base font-bold text-slate-100">Transaction Details</h3>
                <p className="text-xs text-indigo-400 font-mono mt-0.5">{selectedTxId}</p>
              </div>
              <button
                data-testid="detail-close"
                onClick={() => {
                  setSelectedTxId(null);
                  setDetailData(null);
                }}
                className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 transition"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {loadingDetail ? (
              <div className="p-12 text-center text-slate-400 text-sm">Loading transaction details...</div>
            ) : errorDetail ? (
              <div className="p-6 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-lg text-sm">
                {errorDetail}
              </div>
            ) : detailData ? (
              <div className="flex flex-col gap-6 text-sm">
                {/* Transaction Metadata */}
                <div className="bg-slate-800/60 p-4 rounded-xl border border-slate-700/50 flex flex-col gap-3">
                  <div className="flex justify-between items-center border-b border-slate-700/50 pb-2">
                    <span className="text-slate-400 text-xs">Amount</span>
                    <span className="text-lg font-bold text-slate-100">{formatUSD(detailData.transaction?.amount)}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-slate-400 text-xs">Account Name</span>
                    <span className="font-semibold text-slate-200">{detailData.transaction?.account_name || '-'}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-slate-400 text-xs">Region</span>
                    <span className="text-slate-300">{detailData.transaction?.region || '-'}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-slate-400 text-xs">Status</span>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                      detailData.transaction?.status === 'settled' ? 'bg-emerald-500/20 text-emerald-400' :
                      detailData.transaction?.status === 'pending' ? 'bg-amber-500/20 text-amber-400' : 'bg-rose-500/20 text-rose-400'
                    }`}>
                      {detailData.transaction?.status || '-'}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-slate-400 text-xs">Branch</span>
                    <span className="text-slate-300">{detailData.transaction?.branch_name || '-'}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-slate-400 text-xs">Reference</span>
                    <span className="text-slate-300 font-mono text-xs">{detailData.transaction?.reference || '-'}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-slate-400 text-xs">Booked At</span>
                    <span className="text-slate-300 text-xs">{detailData.transaction?.booked_at || '-'}</span>
                  </div>
                </div>

                {/* Audit Event Messages */}
                <div>
                  <h4 className="text-xs font-semibold uppercase text-slate-400 tracking-wider mb-2">Audit Events</h4>
                  {!detailData.events || detailData.events.length === 0 ? (
                    <p className="text-xs text-slate-500 italic">No audit events recorded.</p>
                  ) : (
                    <div className="space-y-2">
                      {detailData.events.map((ev, i) => (
                        <div key={ev.id || i} className="bg-slate-800/40 p-3 rounded-lg border border-slate-700/40 text-xs">
                          <p className="text-slate-200 font-medium">{ev.message}</p>
                          <span className="text-[10px] text-slate-500 mt-1 block">{ev.occurred_at}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Associated Cases & Case Reasons */}
                <div>
                  <h4 className="text-xs font-semibold uppercase text-slate-400 tracking-wider mb-2">Cases & Reasons</h4>
                  {!detailData.cases || detailData.cases.length === 0 ? (
                    <p className="text-xs text-slate-500 italic">No associated cases.</p>
                  ) : (
                    <div className="space-y-2">
                      {detailData.cases.map((c, i) => (
                        <div key={c.id || i} className="bg-slate-800/40 p-3 rounded-lg border border-slate-700/40 text-xs flex flex-col gap-1">
                          <div className="flex justify-between items-center">
                            <span className="font-mono text-indigo-400">{c.id}</span>
                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                              c.severity === 'high' ? 'bg-rose-500/20 text-rose-400' : 'bg-amber-500/20 text-amber-400'
                            }`}>
                              {c.severity}
                            </span>
                          </div>
                          <p className="text-slate-200 font-medium">{c.reason}</p>
                          <div className="text-[10px] text-slate-400 flex justify-between mt-1">
                            <span>Owner: {c.owner}</span>
                            <span className="uppercase">{c.state}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}

      {/* Footer */}
      <footer className="h-8 border-t border-slate-800 bg-slate-950 px-4 flex items-center justify-between text-[11px] text-slate-500 shrink-0">
        <span>Meridian Operations &copy; {new Date().getFullYear()}</span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
          Read-only database access
        </span>
      </footer>
    </div>
  );
}
