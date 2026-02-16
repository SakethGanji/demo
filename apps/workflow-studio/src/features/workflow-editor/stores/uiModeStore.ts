import { create } from 'zustand';

export interface UIMessage {
  id: string;
  type: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  format?: 'text' | 'markdown';
}

interface UIModeState {
  // Chat state
  messages: UIMessage[];
  isExecuting: boolean;

  // HTML panel state
  htmlContent: string | null;

  // Markdown panel state
  markdownContent: string | null;

  // PDF panel state
  pdfBase64: string | null;

  // Table panel state
  tableData: Record<string, unknown>[] | null;

  // Actions
  addMessage: (message: Omit<UIMessage, 'id' | 'timestamp'>) => void;
  clearMessages: () => void;
  setExecuting: (executing: boolean) => void;
  setHtmlContent: (html: string | null) => void;
  setMarkdownContent: (markdown: string | null) => void;
  setPdfBase64: (pdf: string | null) => void;
  setTableData: (data: Record<string, unknown>[] | null) => void;
  reset: () => void;
}

export const useUIModeStore = create<UIModeState>((set) => ({
  messages: [],
  isExecuting: false,
  htmlContent: null,
  markdownContent: null,
  pdfBase64: null,
  tableData: null,

  addMessage: (message) =>
    set((state) => ({
      messages: [
        ...state.messages,
        {
          ...message,
          id: `msg_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
          timestamp: new Date(),
        },
      ],
    })),

  clearMessages: () => set({ messages: [], htmlContent: null, markdownContent: null, pdfBase64: null, tableData: null }),

  setExecuting: (executing) => set({ isExecuting: executing }),

  setHtmlContent: (html) => set({ htmlContent: html }),

  setMarkdownContent: (markdown) => set({ markdownContent: markdown }),

  setPdfBase64: (pdf) => set({ pdfBase64: pdf }),

  setTableData: (data) => set({ tableData: data }),

  reset: () =>
    set({
      messages: [],
      isExecuting: false,
      htmlContent: null,
      markdownContent: null,
      pdfBase64: null,
      tableData: null,
    }),
}));
