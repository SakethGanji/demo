import { useState, useCallback } from 'react';
import { Send, Loader2 } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { useUIModeStore } from '../../stores/uiModeStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import { toBackendWorkflow } from '../../lib/workflowTransform';
import { backends } from '@/shared/lib/config';
import type { UIConfig } from './detectUINodes';
import type { Node, Edge } from 'reactflow';
import type { WorkflowNodeData } from '../../types/workflow';

interface ChatInputProps {
  config: UIConfig;
}

export function ChatInput({ config }: ChatInputProps) {
  const [input, setInput] = useState('');
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const addMessage = useUIModeStore((s) => s.addMessage);
  const isExecuting = useUIModeStore((s) => s.isExecuting);
  const setIsExecuting = useUIModeStore((s) => s.setExecuting);
  const setHtmlContent = useUIModeStore((s) => s.setHtmlContent);
  const setMarkdownContent = useUIModeStore((s) => s.setMarkdownContent);

  const handleSend = useCallback(async () => {
    if (!input.trim() || isExecuting) return;

    const message = input.trim();
    setInput('');

    // Add user message
    addMessage({ type: 'user', content: message });
    setIsExecuting(true);

    const { workflowId: wfId, nodes, edges, workflowName } = useWorkflowStore.getState();

    try {
      let response: Response;

      if (wfId) {
        // Use saved workflow execution
        response = await fetch(`${backends.workflow}/api/workflows/${wfId}/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            input_data: { message },
          }),
        });
      } else {
        // Use ad-hoc execution (no save required)
        const workflow = toBackendWorkflow(
          nodes as Node<WorkflowNodeData>[],
          edges as Edge[],
          workflowName || 'Untitled'
        );

        // Inject the user message into the workflow request
        response = await fetch(`${backends.workflow}/api/workflows/run-adhoc`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...workflow,
            // Pass input data via a special field that the backend will use
            _input_data: { message },
          }),
        });
      }

      if (!response.ok) {
        throw new Error('Workflow execution failed');
      }

      const result = await response.json();

      // Process results
      if (result.data) {
        // First pass: handle explicit display nodes (HTML, Markdown)
        for (const [_nodeName, nodeData] of Object.entries(result.data)) {
          const items = nodeData as Array<{ json: Record<string, unknown> }>;
          if (!items || !items.length) continue;

          for (const item of items) {
            const data = item.json;

            // Handle HTML output
            if (data._renderAs === 'html' && data.html) {
              setHtmlContent(String(data.html));
            }

            // Handle Markdown output
            if (data._renderAs === 'markdown' && data.markdown) {
              setMarkdownContent(String(data.markdown));
            }

            // Handle PDF output
            if (data._renderAs === 'pdf' && data.pdf_base64) {
              useUIModeStore.getState().setPdfBase64(String(data.pdf_base64));
            }

            // Handle Table output
            if (data._renderAs === 'table' && Array.isArray(data.data)) {
              useUIModeStore.getState().setTableData(data.data as Record<string, unknown>[]);
            }
          }
        }

        // Auto-response: extract message from last node's output (n8n-style)
        if (config.autoResponse) {
          const nodeNames = Object.keys(result.data);
          // Skip the ChatInput node itself, get the last actual output
          const lastNodeName = nodeNames[nodeNames.length - 1];
          const lastNodeData = result.data[lastNodeName] as Array<{ json: Record<string, unknown> }>;

          if (lastNodeData?.length > 0) {
            const lastOutput = lastNodeData[lastNodeData.length - 1].json;

            // Extract message from common field names
            const messageFields = ['message', 'output', 'text', 'content', 'response', 'result'];
            let messageContent: string | null = null;

            for (const field of messageFields) {
              if (lastOutput[field] && typeof lastOutput[field] === 'string') {
                messageContent = lastOutput[field] as string;
                break;
              }
            }

            // Fallback: format as JSON code block if no string field found
            if (!messageContent) {
              // Filter out internal fields starting with _
              const cleanOutput: Record<string, unknown> = {};
              for (const [key, value] of Object.entries(lastOutput)) {
                if (!key.startsWith('_')) {
                  cleanOutput[key] = value;
                }
              }

              const keys = Object.keys(cleanOutput);
              if (keys.length === 1 && typeof cleanOutput[keys[0]] === 'string') {
                // Single string field - use it directly
                messageContent = cleanOutput[keys[0]] as string;
              } else if (keys.length > 0) {
                // Multiple fields or non-string - format as JSON code block
                messageContent = '```json\n' + JSON.stringify(cleanOutput, null, 2) + '\n```';
              }
            }

            if (messageContent) {
              addMessage({
                type: 'assistant',
                content: messageContent,
                format: messageContent.startsWith('```') ? 'markdown' : 'text',
              });
            }
          }
        }
      }
    } catch (error) {
      console.error('Execution error:', error);
      addMessage({
        type: 'system',
        content: 'An error occurred while executing the workflow.',
      });
    } finally {
      setIsExecuting(false);
    }
  }, [input, workflowId, addMessage, setIsExecuting, setHtmlContent, setMarkdownContent, isExecuting]);

  const placeholder = config.placeholder || 'Type a message...';

  return (
    <div className="flex gap-2">
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
          }
        }}
        placeholder={placeholder}
        disabled={isExecuting}
        className="flex-1 rounded-lg border bg-background px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary disabled:opacity-50"
      />
      <Button
        onClick={handleSend}
        disabled={!input.trim() || isExecuting}
        size="icon"
        className="h-10 w-10"
      >
        {isExecuting ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Send className="h-4 w-4" />
        )}
      </Button>
    </div>
  );
}
