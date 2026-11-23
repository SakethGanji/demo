/** Meridian Operations — Banking operations management dashboard.
 * Features Overview, Transactions, Accounts, and Exceptions management views.
 * Uses typed API client (`api`) with strict data bindings and verification test hooks. */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { api } from './api/client';
import type {
  OpsSummaryResponse,
  OpsTrendsResponse,
  OpsTransactionsResponse,
  OpsTransactionResponse,
  OpsAccountsResponse,
  OpsCasesResponse,
  OpsCaseActionsResponse
} from './api/client';

type View = 'overview' | 'transactions' | 'accounts' | 'exceptions';

const formatUSD = (val?: number) => {
  if (val === undefined || val === null) return '$0.00';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
};

/** Main Application component for Meridian Operations */
export default function App() {
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [currentView, setCurrentView] = useState<View>('overview');

  // Summary state
  const [summary, setSummary] = useState<OpsSummaryResponse | null>(null);
  const [summaryLoading, setSummaryLoading] = useState<boolean>(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  // Overview data
  const [trends, setTrends] = useState<OpsTrendsResponse | null>(null);
  const [overviewTx, setOverviewTx] = useState<OpsTransactionsResponse | null>(null);
  const [overviewCases, setOverviewCases] = useState<OpsCasesResponse | null>(null);
  const [overviewLoading, setOverviewLoading] = useState<boolean>(false);
  const [overviewError, setOverviewError] = useState<string | null>(null);

  // Transactions tab state
  const [txStatus, setTxStatus] = useState<string>('all');
  const [txRegion, setTxRegion] = useState<string>('all');
  const [txSearch, setTxSearch] = useState<string>('');
  const [txInputStatus, setTxInputStatus] = useState<string>('all');
  const [txInputRegion, setTxInputRegion] = useState<string>('all');
  const [txInputSearch, setTxInputSearch] = useState<string>('');
  const [txPage, setTxPage] = useState<number>(1);
  const [txData, setTxData] = useState<OpsTransactionsResponse | null>(null);
  const [txLoading, setTxLoading] = useState<boolean>(false);
  const [txError, setTxError] = useState<string | null>(null);

  // Accounts tab state
  const [accountsData, setAccountsData] = useState<OpsAccountsResponse | null>(null);
  const [accountsLoading, setAccountsLoading] = useState<boolean>(false);
  const [accountsError, setAccountsError] = useState<string | null>(null);

  // Exceptions tab state
  const [casesData, setCasesData] = useState<OpsCasesResponse | null>(null);
  const [casesLoading, setCasesLoading] = useState<boolean>(false);
  const [casesError, setCasesError] = useState<string | null>(null);

  // Transaction Detail Modal
  const [selectedTxId, setSelectedTxId] = useState<string | null>(null);
  const [txDetail, setTxDetail] = useState<OpsTransactionResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState<boolean>(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Case Editor Modal
  const [editingCase, setEditingCase] = useState<{ id: string; owner: string; state: string } | null>(null);
  const [caseOwnerInput, setCaseOwnerInput] = useState<string>('');
  const [caseStateInput, setCaseStateInput] = useState<string>('');
  const [caseNoteInput, setCaseNoteInput] = useState<string>('');
  const [caseHistory, setCaseHistory] = useState<OpsCaseActionsResponse | null>(null);
  const [caseHistoryLoading, setCaseHistoryLoading] = useState<boolean>(false);
  const [caseHistoryError, setCaseHistoryError] = useState<string | null>(null);
  const [caseSaving, setCaseSaving] = useState<boolean>(false);
  const [caseSaveError, setCaseSaveError] = useState<string | null>(null);
  const [caseValidationError, setCaseValidationError] = useState<string | null>(null);
  const [caseSavedMessage, setCaseSavedMessage] = useState<boolean>(false);

  const reqSeq = useRef<number>(0);
  const openerRef = useRef<HTMLElement | null>(null);

  // Fetch summary data
  const fetchSummary = useCallback(async () => {
    setSummaryLoading(true);
    try {
      const res = await api.opsSummary();
      setSummary(res);
      setSummaryError(null);
    } catch (err: any) {
      setSummaryError(err?.message || 'Failed to fetch summary data');
    } finally {
      setSummaryLoading(false);
    }
  }, []);

  // Fetch Overview Tab
  const loadOverview = useCallback(async () => {
    setOverviewLoading(true);
    try {
      const [tRes, txRes, cRes] = await Promise.all([
        api.opsTrends(),
        api.opsTransactions({ page: 1, page_size: 5 }),
        api.opsCases()
      ]);
      setTrends(tRes);
      setOverviewTx(txRes);
      setOverviewCases(cRes);
      setOverviewError(null);
    } catch (err: any) {
      setOverviewError(err?.message || 'Failed to load overview data');
    } finally {
      setOverviewLoading(false);
    }
  }, []);

  // Fetch Transactions Tab
  const loadTransactions = useCallback(async (pageVal: number, statusVal: string, regionVal: string, searchVal: string) => {
    const currentSeq = ++reqSeq.current;
    setTxLoading(true);
    try {
      const query: Record<string, any> = { page: pageVal, page_size: 25 };
      if (statusVal !== 'all') query.status = statusVal;
      if (regionVal !== 'all') query.region = regionVal;
      if (searchVal !== '') query.search = searchVal;

      const res = await api.opsTransactions(query);
      if (currentSeq === reqSeq.current) {
        setTxData(res);
        setTxError(null);
      }
    } catch (err: any) {
      if (currentSeq === reqSeq.current) {
        setTxError(err?.message || 'Failed to load transactions');
      }
    } finally {
      if (currentSeq === reqSeq.current) {
        setTxLoading(false);
      }
    }
  }, []);

  // Fetch Accounts Tab
  const loadAccounts = useCallback(async () => {
    setAccountsLoading(true);
    try {
      const res = await api.opsAccounts();
      setAccountsData(res);
      setAccountsError(null);
    } catch (err: any) {
      setAccountsError(err?.message || 'Failed to load accounts');
    } finally {
      setAccountsLoading(false);
    }
  }, []);

  // Fetch Cases Tab
  const loadCases = useCallback(async () => {
    setCasesLoading(true);
    try {
      const res = await api.opsCases();
      setCasesData(res);
      setCasesError(null);
    } catch (err: any) {
      setCasesError(err?.message || 'Failed to load cases');
    } finally {
      setCasesLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchSummary();
    loadOverview();
  }, [fetchSummary, loadOverview]);

  // View switcher side-effects
  const handleViewChange = (newView: View) => {
    setCurrentView(newView);
    fetchSummary();
    if (newView === 'overview') loadOverview();
    if (newView === 'transactions') loadTransactions(1, txStatus, txRegion, txSearch);
    if (newView === 'accounts') loadAccounts();
    if (newView === 'exceptions') loadCases();
  };

  const handleGlobalRefresh = () => {
    fetchSummary();
    if (currentView === 'overview') loadOverview();
    if (currentView === 'transactions') loadTransactions(txPage, txStatus, txRegion, txSearch);
    if (currentView === 'accounts') loadAccounts();
    if (currentView === 'exceptions') loadCases();
  };

  const isGlobalLoading = summaryLoading || overviewLoading || txLoading || accountsLoading || casesLoading;

  // Transaction detail trigger
  const openTransactionDetail = async (id: string, e?: React.MouseEvent) => {
    if (e?.currentTarget) {
      openerRef.current = e.currentTarget as HTMLElement;
    } else {
      openerRef.current = document.activeElement as HTMLElement;
    }
    setSelectedTxId(id);
    setDetailLoading(true);
    setDetailError(null);
    setTxDetail(null);
    try {
      const res = await api.opsTransaction({ id });
      setTxDetail(res);
    } catch (err: any) {
      setDetailError(err?.message || 'Failed to load transaction detail');
    } finally {
      setDetailLoading(false);
    }
  };

  const closeTransactionDetail = () => {
    setSelectedTxId(null);
    if (openerRef.current) {
      openerRef.current.focus();
    }
  };

  const refreshCaseHistory = async (id: string) => {
    setCaseHistoryLoading(true);
    setCaseHistoryError(null);
    try {
      const res = await api.opsCaseActions({ id });
      setCaseHistory(res);
    } catch (err: any) {
      setCaseHistoryError(err?.message || 'Failed to load case action history');
    } finally {
      setCaseHistoryLoading(false);
    }
  };

  // Case editor trigger
  const openCaseEditor = async (c: { id: string; owner?: string; state?: string }, e?: React.MouseEvent) => {
    if (e?.currentTarget) {
      openerRef.current = e.currentTarget as HTMLElement;
    } else {
      openerRef.current = document.activeElement as HTMLElement;
    }
    setEditingCase({ id: c.id, owner: c.owner || '', state: c.state || 'open' });
    setCaseOwnerInput(c.owner || '');
    setCaseStateInput(c.state || 'open');
    setCaseNoteInput('');
    setCaseValidationError(null);
    setCaseSaveError(null);
    setCaseSavedMessage(false);
    setCaseHistory(null);
    
    refreshCaseHistory(c.id);
  };

  const closeCaseEditor = () => {
    setEditingCase(null);
    if (openerRef.current) {
      openerRef.current.focus();
    }
  };

  const handleSaveCase = async () => {
    setCaseValidationError(null);
    setCaseSaveError(null);
    setCaseSavedMessage(false);

    const trimmedOwner = caseOwnerInput.trim();
    const trimmedNote = caseNoteInput.trim();

    if (!trimmedOwner || !trimmedNote) {
      setCaseValidationError('Owner and Resolution Note are required.');
      return;
    }

    if (!editingCase) return;

    setCaseSaving(true);
    try {
      await api.opsUpdateCase(
        { id: editingCase.id },
        { owner: trimmedOwner, state: caseStateInput, note: trimmedNote }
      );
      setCaseSavedMessage(true);
      
      // Update local state and history
      setEditingCase(prev => prev ? { ...prev, owner: trimmedOwner, state: caseStateInput } : null);
      if (currentView === 'exceptions') loadCases();
      if (currentView === 'overview') loadOverview();
      fetchSummary();
      await refreshCaseHistory(editingCase.id);
    } catch (err: any) {
      setCaseSaveError(err?.message || 'Failed to update case');
    } finally {
      setCaseSaving(false);
    }
  };

  // Apply transactions filters
  const handleApplyFilters = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setTxStatus(txInputStatus);
    setTxRegion(txInputRegion);
    setTxSearch(txInputSearch);
    setTxPage(1);
    loadTransactions(1, txInputStatus, txInputRegion, txInputSearch);
  };

  const handlePageChange = (newPage: number) => {
    setTxPage(newPage);
    loadTransactions(newPage, txStatus, txRegion, txSearch);
  };

  // Close overlays on escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (editingCase) closeCaseEditor();
        else if (selectedTxId) closeTransactionDetail();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [editingCase, selectedTxId]);

  const isDark = theme === 'dark';

  return (
    <div className={`h-full flex flex-col font-sans transition-colors duration-200 overflow-x-hidden ${isDark ? 'bg-slate-900 text-slate-100' : 'bg-slate-50 text-slate-800'}`}>
      
      {/* Top Header */}
      <header className={`px-4 sm:px-6 py-3 border-b flex flex-wrap items-center justify-between gap-3 shrink-0 ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200 shadow-sm'}`}>
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white font-bold text-lg shadow">
            M
          </div>
          <div>
            <h1 className="font-bold text-base sm:text-lg leading-none tracking-tight">Meridian Operations</h1>
            <span className="text-xs text-indigo-500 font-medium">Core Banking Portal</span>
          </div>
        </div>

        <div className="flex items-center space-x-2 sm:space-x-4 text-xs font-medium flex-wrap gap-y-2">
          <span className={`px-2.5 py-1 rounded-full flex items-center gap-1.5 ${isDark ? 'bg-slate-700 text-slate-300' : 'bg-slate-100 text-slate-600'}`}>
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            Synthetic Data / USD
          </span>

          <button
            data-testid="app-refresh"
            disabled={isGlobalLoading}
            onClick={handleGlobalRefresh}
            className={`px-3 py-1.5 rounded-md flex items-center gap-1 transition disabled:opacity-50 ${isDark ? 'bg-slate-700 hover:bg-slate-600 text-slate-200' : 'bg-slate-100 hover:bg-slate-200 text-slate-700'}`}
            title="Refresh View"
          >
            <svg className={`w-3.5 h-3.5 ${isGlobalLoading ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
            Refresh
          </button>

          <button
            data-testid="theme-toggle"
            onClick={() => setTheme(isDark ? 'light' : 'dark')}
            className={`px-3 py-1.5 rounded-md transition ${isDark ? 'bg-slate-700 text-amber-300 hover:bg-slate-600' : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`}
          >
            {isDark ? '☀️ Light' : '🌙 Dark'}
          </button>
        </div>
      </header>

      {/* Main Body */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0 overflow-hidden">
        
        {/* Navigation Sidebar / Top Tabs */}
        <nav className={`w-full md:w-56 border-b md:border-b-0 md:border-r flex flex-row md:flex-col p-2 md:p-3 shrink-0 overflow-x-auto md:overflow-x-visible gap-1 md:gap-1 ${isDark ? 'bg-slate-800/50 border-slate-700' : 'bg-white border-slate-200'}`}>
          {[
            { id: 'overview', label: 'Overview', testId: 'nav-overview', icon: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6' },
            { id: 'transactions', label: 'Transactions', testId: 'nav-transactions', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2' },
            { id: 'accounts', label: 'Accounts', testId: 'nav-accounts', icon: 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4' },
            { id: 'exceptions', label: 'Exceptions', testId: 'nav-exceptions', icon: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z' }
          ].map(item => {
            const active = currentView === item.id;
            return (
              <button
                key={item.id}
                data-testid={item.testId}
                onClick={() => handleViewChange(item.id as View)}
                className={`whitespace-nowrap px-3 py-2 md:py-2.5 rounded-lg font-medium text-xs sm:text-sm flex items-center space-x-2 transition shrink-0 ${
                  active 
                    ? 'bg-indigo-600 text-white shadow-sm' 
                    : isDark 
                      ? 'text-slate-300 hover:bg-slate-700/60' 
                      : 'text-slate-600 hover:bg-slate-100'
                }`}
              >
                <svg className="w-4 h-4 sm:w-5 sm:h-5 opacity-80 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={item.icon} />
                </svg>
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        {/* Dynamic Content Area */}
        <main className="flex-1 min-w-0 min-h-0 overflow-y-auto p-4 sm:p-6 flex flex-col">
          
          {/* OVERVIEW VIEW */}
          {currentView === 'overview' && (
            <div className="space-y-6">
              
              {/* Summary KPIs */}
              <div>
                <h2 className="text-xl font-bold mb-4">Operations Overview</h2>
                {summaryError && (
                  <div className="p-3 mb-4 text-sm text-red-600 bg-red-50 dark:bg-red-950/40 dark:text-red-400 rounded-lg flex items-center justify-between">
                    <span>{summaryError}</span>
                    <button onClick={handleGlobalRefresh} className="underline text-xs font-semibold hover:opacity-80">Retry</button>
                  </div>
                )}
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 sm:gap-4">
                  <div className={`p-4 rounded-xl border ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                    <div className="text-xs text-slate-500 dark:text-slate-400 font-medium">Volume</div>
                    <div className="text-lg sm:text-xl font-bold mt-1 text-emerald-600 dark:text-emerald-400 truncate">
                      {summaryLoading ? '...' : formatUSD(summary?.volume)}
                    </div>
                  </div>

                  <div className={`p-4 rounded-xl border ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                    <div className="text-xs text-slate-500 dark:text-slate-400 font-medium">Transactions</div>
                    <div className="text-lg sm:text-xl font-bold mt-1" data-testid="kpi-transactions">
                      {summaryLoading ? '...' : (summary?.transactions ?? 0)}
                    </div>
                  </div>

                  <div className={`p-4 rounded-xl border ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                    <div className="text-xs text-slate-500 dark:text-slate-400 font-medium">Pending</div>
                    <div className="text-lg sm:text-xl font-bold mt-1 text-amber-500">
                      {summaryLoading ? '...' : (summary?.pending ?? 0)}
                    </div>
                  </div>

                  <div className={`p-4 rounded-xl border ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                    <div className="text-xs text-slate-500 dark:text-slate-400 font-medium">Exceptions</div>
                    <div className="text-lg sm:text-xl font-bold mt-1 text-red-500" data-testid="kpi-exceptions">
                      {summaryLoading ? '...' : (summary?.exceptions ?? 0)}
                    </div>
                  </div>

                  <div className={`p-4 rounded-xl border ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                    <div className="text-xs text-slate-500 dark:text-slate-400 font-medium">Accounts</div>
                    <div className="text-lg sm:text-xl font-bold mt-1">
                      {summaryLoading ? '...' : (summary?.accounts ?? 0)}
                    </div>
                  </div>

                  <div className={`p-4 rounded-xl border ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                    <div className="text-xs text-slate-500 dark:text-slate-400 font-medium">Branches</div>
                    <div className="text-lg sm:text-xl font-bold mt-1">
                      {summaryLoading ? '...' : (summary?.branches ?? 0)}
                    </div>
                  </div>
                </div>
              </div>

              {/* Volume Trends Chart */}
              <div className={`p-4 sm:p-5 rounded-xl border ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                <h3 className="text-sm font-semibold mb-4 text-slate-500 dark:text-slate-400">15-Day Transaction Volume</h3>
                {overviewLoading && <div className="py-8 text-center text-sm text-slate-400">Loading chart...</div>}
                {overviewError && (
                  <div className="p-3 text-sm text-red-600 bg-red-50 dark:bg-red-950/40 dark:text-red-400 rounded-lg flex items-center justify-between">
                    <span>{overviewError}</span>
                    <button onClick={handleGlobalRefresh} className="underline text-xs font-semibold">Retry</button>
                  </div>
                )}
                {!overviewLoading && !overviewError && (
                  <div data-testid="volume-chart" className="w-full min-w-0">
                    {(() => {
                      const trendItems = (trends?.items || []).slice(0, 15);
                      const maxVol = Math.max(...trendItems.map(t => t.volume || 0), 1);
                      return (
                        <div className="w-full">
                          <div className="h-[180px] w-full flex items-end justify-between gap-1 border-b border-slate-200 dark:border-slate-700 pb-0">
                            {trendItems.map((item, idx) => {
                              const vol = item.volume || 0;
                              const pxHeight = Math.max(2, Math.round((180 * vol) / maxVol));
                              const titleStr = `${item.day || ''}: ${formatUSD(vol)}`;
                              return (
                                <div key={idx} className="flex-1 flex flex-col items-center justify-end h-full min-w-0">
                                  <div
                                    data-testid="trend-bar"
                                    title={titleStr}
                                    style={{ height: `${pxHeight}px` }}
                                    className="w-full bg-indigo-600 dark:bg-indigo-500 rounded-t hover:bg-indigo-400 transition-all cursor-pointer shrink-0"
                                  />
                                </div>
                              );
                            })}
                          </div>
                          <div className="flex justify-between gap-1 mt-2 w-full">
                            {trendItems.map((item, idx) => (
                              <span key={idx} className="flex-1 text-[10px] text-slate-500 dark:text-slate-400 truncate text-center shrink-0">
                                {item.day ? item.day.split('-').slice(1).join('/') : ''}
                              </span>
                            ))}
                          </div>
                        </div>
                      );
                    })()}
                  </div>
                )}
              </div>

              {/* Grid: Recent Transactions & Exception Queue */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                
                {/* Recent Transactions */}
                <div className={`p-4 sm:p-5 rounded-xl border ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                  <h3 className="text-sm font-semibold mb-3 text-slate-500 dark:text-slate-400">Recent Activity</h3>
                  <div className="divide-y divide-slate-200 dark:divide-slate-700">
                    {(overviewTx?.items || []).slice(0, 5).map(tx => (
                      <div key={tx.id} className="py-2.5 flex items-center justify-between text-sm gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="font-medium truncate">{tx.account_name}</div>
                          <div className="text-xs text-slate-400 truncate">{tx.reference} • {tx.region}</div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="font-semibold">{formatUSD(tx.amount)}</div>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                            tx.status === 'settled' ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300' : 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300'
                          }`}>
                            {tx.status}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Exception Queue */}
                <div className={`p-4 sm:p-5 rounded-xl border ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                  <h3 className="text-sm font-semibold mb-3 text-slate-500 dark:text-slate-400">Open Exceptions</h3>
                  <div className="divide-y divide-slate-200 dark:divide-slate-700">
                    {(overviewCases?.items || []).slice(0, 5).map(c => (
                      <div key={c.id} className="py-2.5 flex items-center justify-between text-sm gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="font-medium flex items-center gap-2 flex-wrap">
                            <span className="truncate">{c.reason}</span>
                            <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 ${
                              c.severity === 'high' ? 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300' : 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300'
                            }`}>
                              {c.severity}
                            </span>
                          </div>
                          <div className="text-xs text-slate-400 truncate">Owner: {c.owner || 'Unassigned'}</div>
                        </div>
                        <div className="text-right shrink-0">
                          <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">{c.state}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

              </div>
            </div>
          )}

          {/* TRANSACTIONS VIEW */}
          {currentView === 'transactions' && (
            <div className="space-y-4">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                  <h2 className="text-xl font-bold">Transactions Ledger</h2>
                  <div className="text-xs text-slate-500 dark:text-slate-400">
                    Total count: <span data-testid="transactions-total">{txData?.total ?? 0}</span>
                  </div>
                </div>

                {/* Filter Controls */}
                <form onSubmit={handleApplyFilters} className="flex flex-wrap items-center gap-2">
                  <select
                    data-testid="status-filter"
                    value={txInputStatus}
                    onChange={e => setTxInputStatus(e.target.value)}
                    className={`text-sm px-3 py-1.5 rounded-lg border ${isDark ? 'bg-slate-800 border-slate-700 text-slate-200' : 'bg-white border-slate-300 text-slate-800'}`}
                  >
                    <option value="all">all status</option>
                    <option value="settled">settled</option>
                    <option value="pending">pending</option>
                    <option value="exception">exception</option>
                  </select>

                  <select
                    data-testid="region-filter"
                    value={txInputRegion}
                    onChange={e => setTxInputRegion(e.target.value)}
                    className={`text-sm px-3 py-1.5 rounded-lg border ${isDark ? 'bg-slate-800 border-slate-700 text-slate-200' : 'bg-white border-slate-300 text-slate-800'}`}
                  >
                    <option value="all">all regions</option>
                    <option value="Americas">Americas</option>
                    <option value="EMEA">EMEA</option>
                    <option value="APAC">APAC</option>
                  </select>

                  <input
                    type="text"
                    data-testid="transaction-search"
                    placeholder="Search transactions..."
                    value={txInputSearch}
                    onChange={e => setTxInputSearch(e.target.value)}
                    className={`text-sm px-3 py-1.5 rounded-lg border min-w-[140px] ${isDark ? 'bg-slate-800 border-slate-700 text-slate-200' : 'bg-white border-slate-300 text-slate-800'}`}
                  />

                  <button
                    type="submit"
                    data-testid="apply-filters"
                    className="px-4 py-1.5 text-sm bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg transition shrink-0"
                  >
                    Apply
                  </button>
                </form>
              </div>

              {txError && (
                <div className="p-3 text-sm text-red-600 bg-red-50 dark:bg-red-950/40 dark:text-red-400 rounded-lg flex justify-between items-center">
                  <span>{txError}</span>
                  <button onClick={() => loadTransactions(txPage, txStatus, txRegion, txSearch)} className="underline text-xs font-semibold">Retry</button>
                </div>
              )}

              {/* Transactions Table */}
              <div className={`rounded-xl border overflow-hidden ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                {txLoading ? (
                  <div className="p-8 text-center text-sm text-slate-400">Loading transactions...</div>
                ) : (!txData?.items || txData.items.length === 0) ? (
                  <div data-testid="empty-state" className="p-12 text-center text-slate-500 dark:text-slate-400">
                    No transactions found matching criteria.
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead className={`text-xs uppercase border-b ${isDark ? 'bg-slate-800/80 border-slate-700 text-slate-400' : 'bg-slate-50 border-slate-200 text-slate-500'}`}>
                        <tr>
                          <th className="px-4 py-3">Reference / ID</th>
                          <th className="px-4 py-3">Account</th>
                          <th className="px-4 py-3">Branch</th>
                          <th className="px-4 py-3">Region</th>
                          <th className="px-4 py-3">Amount</th>
                          <th className="px-4 py-3">Status</th>
                          <th className="px-4 py-3 text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                        {txData.items.map(tx => (
                          <tr
                            key={tx.id}
                            data-testid="transaction-row"
                            data-id={tx.id}
                            className={`hover:bg-slate-50/50 dark:hover:bg-slate-700/50 transition cursor-pointer`}
                            onClick={(e) => tx.id && openTransactionDetail(tx.id, e)}
                          >
                            <td className="px-4 py-3 font-mono text-xs">{tx.reference || tx.id}</td>
                            <td className="px-4 py-3 font-medium">{tx.account_name}</td>
                            <td className="px-4 py-3 text-slate-500 dark:text-slate-400">{tx.branch_name}</td>
                            <td className="px-4 py-3">{tx.region}</td>
                            <td className="px-4 py-3 font-semibold">{formatUSD(tx.amount)}</td>
                            <td className="px-4 py-3">
                              <span className={`text-xs px-2 py-0.5 rounded font-medium ${
                                tx.status === 'settled' ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300' :
                                tx.status === 'pending' ? 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300' :
                                'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300'
                              }`}>
                                {tx.status}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-right">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  tx.id && openTransactionDetail(tx.id, e);
                                }}
                                className="text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 font-medium"
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
              </div>

              {/* Pagination Controls */}
              {txData && (
                <div className="flex items-center justify-between py-2 text-sm flex-wrap gap-2">
                  <div className="text-xs sm:text-sm">
                    Page <span data-testid="current-page">{txPage}</span> of {Math.ceil((txData.total || 0) / 25) || 1}
                  </div>
                  <div className="flex space-x-2">
                    <button
                      data-testid="page-prev"
                      disabled={txPage <= 1 || txLoading}
                      onClick={() => handlePageChange(txPage - 1)}
                      className="px-3 py-1.5 text-xs sm:text-sm rounded border border-slate-300 dark:border-slate-700 disabled:opacity-50 hover:bg-slate-100 dark:hover:bg-slate-800"
                    >
                      Previous
                    </button>
                    <button
                      data-testid="page-next"
                      disabled={txPage >= Math.ceil((txData.total || 0) / 25) || txLoading}
                      onClick={() => handlePageChange(txPage + 1)}
                      className="px-3 py-1.5 text-xs sm:text-sm rounded border border-slate-300 dark:border-slate-700 disabled:opacity-50 hover:bg-slate-100 dark:hover:bg-slate-800"
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ACCOUNTS VIEW */}
          {currentView === 'accounts' && (
            <div className="space-y-4">
              <h2 className="text-xl font-bold">Accounts Overview</h2>
              {accountsError && (
                <div className="p-3 text-sm text-red-600 bg-red-50 dark:bg-red-950/40 dark:text-red-400 rounded-lg flex justify-between items-center">
                  <span>{accountsError}</span>
                  <button onClick={loadAccounts} className="underline text-xs font-semibold">Retry</button>
                </div>
              )}

              <div className={`rounded-xl border overflow-hidden ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                {accountsLoading ? (
                  <div className="p-8 text-center text-sm text-slate-400">Loading accounts...</div>
                ) : (!accountsData?.items || accountsData.items.length === 0) ? (
                  <div data-testid="empty-state" className="p-12 text-center text-slate-500 dark:text-slate-400">No accounts found.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead className={`text-xs uppercase border-b ${isDark ? 'bg-slate-800/80 border-slate-700 text-slate-400' : 'bg-slate-50 border-slate-200 text-slate-500'}`}>
                        <tr>
                          <th className="px-4 py-3">Client / Name</th>
                          <th className="px-4 py-3">Branch</th>
                          <th className="px-4 py-3">Region</th>
                          <th className="px-4 py-3">Tx Count</th>
                          <th className="px-4 py-3 text-right">Balance</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                        {accountsData.items.map((acc, idx) => (
                          <tr key={acc.id || idx} data-testid="account-row" className="hover:bg-slate-50/50 dark:hover:bg-slate-700/50">
                            <td className="px-4 py-3 font-medium">{acc.name}</td>
                            <td className="px-4 py-3 text-slate-500 dark:text-slate-400">{acc.branch_name}</td>
                            <td className="px-4 py-3">{acc.region}</td>
                            <td className="px-4 py-3">{acc.transaction_count}</td>
                            <td className="px-4 py-3 text-right font-semibold">{formatUSD(acc.balance)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* EXCEPTIONS VIEW */}
          {currentView === 'exceptions' && (
            <div className="space-y-4">
              <h2 className="text-xl font-bold">Exceptions Management</h2>
              {casesError && (
                <div className="p-3 text-sm text-red-600 bg-red-50 dark:bg-red-950/40 dark:text-red-400 rounded-lg flex justify-between items-center">
                  <span>{casesError}</span>
                  <button onClick={loadCases} className="underline text-xs font-semibold">Retry</button>
                </div>
              )}

              <div className={`rounded-xl border overflow-hidden ${isDark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}>
                {casesLoading ? (
                  <div className="p-8 text-center text-sm text-slate-400">Loading cases...</div>
                ) : (!casesData?.items || casesData.items.length === 0) ? (
                  <div data-testid="empty-state" className="p-12 text-center text-slate-500 dark:text-slate-400">No cases found.</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead className={`text-xs uppercase border-b ${isDark ? 'bg-slate-800/80 border-slate-700 text-slate-400' : 'bg-slate-50 border-slate-200 text-slate-500'}`}>
                        <tr>
                          <th className="px-4 py-3">Case ID</th>
                          <th className="px-4 py-3">Account</th>
                          <th className="px-4 py-3">Reason</th>
                          <th className="px-4 py-3">Severity</th>
                          <th className="px-4 py-3">Owner</th>
                          <th className="px-4 py-3">State</th>
                          <th className="px-4 py-3">Amount</th>
                          <th className="px-4 py-3 text-right">Actions</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                        {casesData.items.map(c => (
                          <tr key={c.id} data-testid="case-row" className="hover:bg-slate-50/50 dark:hover:bg-slate-700/50">
                            <td className="px-4 py-3 font-mono text-xs">{c.id}</td>
                            <td className="px-4 py-3 font-medium">{c.account_name}</td>
                            <td className="px-4 py-3">{c.reason}</td>
                            <td className="px-4 py-3">
                              <span className={`text-xs px-2 py-0.5 rounded font-medium ${
                                c.severity === 'high' ? 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300' : 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300'
                              }`}>
                                {c.severity}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-slate-500 dark:text-slate-400">{c.owner || 'Unassigned'}</td>
                            <td className="px-4 py-3 font-medium">{c.state}</td>
                            <td className="px-4 py-3 font-semibold">{formatUSD(c.amount)}</td>
                            <td className="px-4 py-3 text-right space-x-2 whitespace-nowrap">
                              {c.transaction_id && (
                                <button
                                  onClick={(e) => openTransactionDetail(c.transaction_id!, e)}
                                  className="text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-500 font-medium mr-2"
                                >
                                  Tx Detail
                                </button>
                              )}
                              <button
                                data-testid={`edit-case-${c.id}`}
                                onClick={(e) => openCaseEditor(c, e)}
                                className="text-xs px-2.5 py-1 bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded transition"
                              >
                                Manage case
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}

        </main>
      </div>

      {/* TRANSACTION DETAIL MODAL / SLIDE-OVER */}
      {selectedTxId && (
        <div data-testid="transaction-detail" className="fixed inset-0 z-50 flex items-center justify-end bg-black/50 backdrop-blur-sm">
          <div className={`w-full max-w-lg h-full p-6 flex flex-col justify-between overflow-y-auto ${isDark ? 'bg-slate-800 text-slate-100' : 'bg-white text-slate-800'}`}>
            <div className="space-y-6">
              <div className="flex items-center justify-between border-b pb-4 dark:border-slate-700">
                <h3 className="text-lg font-bold">Transaction Details</h3>
                <button
                  data-testid="detail-close"
                  onClick={closeTransactionDetail}
                  className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 text-xl font-bold p-1"
                >
                  ✕
                </button>
              </div>

              {detailLoading && <div className="text-center py-8 text-slate-400">Loading details...</div>}
              {detailError && (
                <div className="p-3 text-sm text-red-600 bg-red-50 dark:bg-red-950/40 dark:text-red-400 rounded-lg">
                  {detailError}
                </div>
              )}

              {txDetail && txDetail.transaction && (
                <div className="space-y-6">
                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <div className="text-xs text-slate-400">Transaction ID</div>
                      <div className="font-mono font-medium">{txDetail.transaction.id}</div>
                    </div>
                    <div>
                      <div className="text-xs text-slate-400">Account Name</div>
                      <div className="font-medium">{txDetail.transaction.account_name}</div>
                    </div>
                    <div>
                      <div className="text-xs text-slate-400">Region</div>
                      <div>{txDetail.transaction.region}</div>
                    </div>
                    <div>
                      <div className="text-xs text-slate-400">Amount</div>
                      <div className="font-semibold text-lg">{formatUSD(txDetail.transaction.amount)}</div>
                    </div>
                    <div>
                      <div className="text-xs text-slate-400">Status</div>
                      <div className="font-medium capitalize">{txDetail.transaction.status}</div>
                    </div>
                  </div>

                  {/* Audit Events */}
                  <div>
                    <h4 className="text-xs font-semibold uppercase text-slate-400 tracking-wider mb-2">Audit Events</h4>
                    <div className="space-y-2">
                      {(txDetail.events || []).length === 0 ? (
                        <div className="text-xs text-slate-400">No audit events logged.</div>
                      ) : (
                        txDetail.events?.map(ev => (
                          <div key={ev.id} className={`p-2.5 rounded text-xs ${isDark ? 'bg-slate-700/50' : 'bg-slate-100'}`}>
                            <div>{ev.message}</div>
                            <div className="text-[10px] text-slate-400 mt-1">{ev.occurred_at}</div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>

                  {/* Linked Cases */}
                  <div>
                    <h4 className="text-xs font-semibold uppercase text-slate-400 tracking-wider mb-2">Linked Cases</h4>
                    <div className="space-y-2">
                      {(txDetail.cases || []).length === 0 ? (
                        <div className="text-xs text-slate-400">No linked cases.</div>
                      ) : (
                        txDetail.cases?.map(c => (
                          <div key={c.id} className={`p-2.5 rounded text-xs ${isDark ? 'bg-slate-700/50' : 'bg-slate-100'}`}>
                            <div className="font-medium">{c.reason}</div>
                            <div className="text-slate-400 mt-0.5">Owner: {c.owner || 'Unassigned'} • Severity: {c.severity}</div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>

            <button
              onClick={closeTransactionDetail}
              className="mt-6 w-full py-2 bg-slate-200 dark:bg-slate-700 text-sm font-medium rounded-lg hover:opacity-90 transition"
            >
              Close
            </button>
          </div>
        </div>
      )}

      {/* CASE EDITOR DIALOG */}
      {editingCase && (
        <div
          data-testid="case-editor"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
        >
          <div className={`w-full max-w-lg rounded-xl shadow-2xl p-6 space-y-4 max-h-[90vh] flex flex-col ${isDark ? 'bg-slate-800 text-slate-100 border border-slate-700' : 'bg-white text-slate-800'}`}>
            <div className="flex justify-between items-center border-b pb-3 dark:border-slate-700">
              <h3 className="font-bold text-lg">Manage Case ({editingCase.id})</h3>
              <button
                onClick={closeCaseEditor}
                className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 text-lg font-bold"
              >
                ✕
              </button>
            </div>

            {caseSavedMessage && (
              <div data-testid="case-saved" className="p-3 text-sm text-emerald-700 bg-emerald-50 dark:bg-emerald-950/40 dark:text-emerald-300 rounded-lg">
                Case updated successfully!
              </div>
            )}

            {caseValidationError && (
              <div role="alert" className="p-3 text-sm text-red-600 bg-red-50 dark:bg-red-950/40 dark:text-red-400 rounded-lg">
                {caseValidationError}
              </div>
            )}

            {caseSaveError && (
              <div role="alert" className="p-3 text-sm text-red-600 bg-red-50 dark:bg-red-950/40 dark:text-red-400 rounded-lg">
                {caseSaveError}
              </div>
            )}

            <div className="space-y-4 flex-1 overflow-y-auto pr-1">
              <div>
                <label className="block text-xs font-semibold mb-1 text-slate-500 dark:text-slate-400">State</label>
                <select
                  data-testid="case-state"
                  value={caseStateInput}
                  onChange={e => setCaseStateInput(e.target.value)}
                  className={`w-full text-sm px-3 py-2 rounded-lg border ${isDark ? 'bg-slate-700 border-slate-600 text-slate-100' : 'bg-slate-50 border-slate-300 text-slate-800'}`}
                >
                  <option value="open">open</option>
                  <option value="in_review">in_review</option>
                  <option value="resolved">resolved</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold mb-1 text-slate-500 dark:text-slate-400">Owner</label>
                <input
                  type="text"
                  data-testid="case-owner"
                  value={caseOwnerInput}
                  onChange={e => setCaseOwnerInput(e.target.value)}
                  className={`w-full text-sm px-3 py-2 rounded-lg border ${isDark ? 'bg-slate-700 border-slate-600 text-slate-100' : 'bg-slate-50 border-slate-300 text-slate-800'}`}
                />
              </div>

              <div>
                <label className="block text-xs font-semibold mb-1 text-slate-500 dark:text-slate-400">Resolution Note (Required)</label>
                <textarea
                  data-testid="case-note"
                  rows={3}
                  value={caseNoteInput}
                  onChange={e => setCaseNoteInput(e.target.value)}
                  placeholder="Enter resolution notes..."
                  className={`w-full text-sm px-3 py-2 rounded-lg border ${isDark ? 'bg-slate-700 border-slate-600 text-slate-100' : 'bg-slate-50 border-slate-300 text-slate-800'}`}
                />
              </div>

              {/* Case History */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-xs font-semibold uppercase text-slate-400 tracking-wider">Case Action History</h4>
                  {caseHistoryError && (
                    <button
                      onClick={() => refreshCaseHistory(editingCase.id)}
                      className="text-xs text-indigo-500 hover:underline font-semibold"
                    >
                      Retry history
                    </button>
                  )}
                </div>

                {caseHistoryError && (
                  <div role="alert" className="p-2 mb-2 text-xs text-red-600 bg-red-50 dark:bg-red-950/40 dark:text-red-400 rounded">
                    {caseHistoryError}
                  </div>
                )}

                <div data-testid="case-history" className="space-y-2 max-h-36 overflow-y-auto">
                  {caseHistoryLoading ? (
                    <div className="text-xs text-slate-400">Loading history...</div>
                  ) : (!caseHistory?.items || caseHistory.items.length === 0) ? (
                    <div className="text-xs text-slate-400">No action history recorded.</div>
                  ) : (
                    caseHistory.items.map((act, idx) => (
                      <div key={act.id || idx} className={`p-2 rounded text-xs ${isDark ? 'bg-slate-700/50' : 'bg-slate-100'}`}>
                        <div className="font-medium">{act.note}</div>
                        <div className="text-[10px] text-slate-400 mt-1">
                          Owner: {act.owner} • State: {act.state} • {act.occurred_at}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>

            <div className="flex justify-end space-x-2 pt-3 border-t dark:border-slate-700">
              <button
                type="button"
                data-testid="case-cancel"
                onClick={closeCaseEditor}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-700 transition"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="case-save"
                disabled={caseSaving}
                onClick={handleSaveCase}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-50 transition"
              >
                {caseSaving ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Compact Footer */}
      <footer className={`px-4 sm:px-6 py-2 text-center text-xs border-t shrink-0 ${isDark ? 'bg-slate-800 border-slate-700 text-slate-400' : 'bg-slate-100 border-slate-200 text-slate-500'}`}>
        Meridian Operations • Synthetic Banking Environment • Confidential Operational Portal
      </footer>

    </div>
  );
}
