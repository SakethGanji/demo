import { useState, useCallback } from 'react';
import { Link, Copy, Check, Terminal } from 'lucide-react';
import type { Node } from '@xyflow/react';
import type { WorkflowNodeData } from '../../../types/workflow';
import { useWorkflowStore } from '../../../stores/workflowStore';
import { backends } from '@/shared/lib/config';
import type { ParsedCurlResult } from '../../../lib/parseCurl';
import ImportCurlDialog from '../ImportCurlDialog';

// --- Shared interface ---

interface NodeExtensionProps {
  node: Node<WorkflowNodeData>;
}

type NodeExtensionComponent = React.ComponentType<NodeExtensionProps>;

// --- Registry ---

const nodeExtensions: Record<string, NodeExtensionComponent[]> = {
  Webhook: [WebhookUrlExtension],
  HttpRequest: [CurlImportExtension],
};

export function getNodeExtensions(nodeType: string): NodeExtensionComponent[] {
  return nodeExtensions[nodeType] ?? [];
}

// --- WebhookUrlExtension ---

function WebhookUrlExtension({ node }: NodeExtensionProps) {
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const [copiedUrl, setCopiedUrl] = useState(false);
  const [copiedPathUrl, setCopiedPathUrl] = useState(false);

  const webhookUrl = workflowId ? `${backends.workflow}/webhook/${workflowId}` : null;
  const webhookPath = (node.data.parameters as Record<string, unknown>)?.path as string | undefined;
  const webhookPathUrl = webhookPath ? `${backends.workflow}/webhook/p/${webhookPath}` : null;

  const copyWebhookUrl = useCallback(() => {
    if (webhookUrl) {
      navigator.clipboard.writeText(webhookUrl);
      setCopiedUrl(true);
      setTimeout(() => setCopiedUrl(false), 2000);
    }
  }, [webhookUrl]);

  const copyWebhookPathUrl = useCallback(() => {
    if (webhookPathUrl) {
      navigator.clipboard.writeText(webhookPathUrl);
      setCopiedPathUrl(true);
      setTimeout(() => setCopiedPathUrl(false), 2000);
    }
  }, [webhookPathUrl]);

  return (
    <div className="rounded-md border border-border bg-muted/30">
      <div className="px-3 py-2">
        <div className="flex items-center gap-2 mb-2">
          <Link size={14} className="text-muted-foreground" />
          <span className="text-sm font-medium text-foreground">Webhook URL</span>
        </div>
        {webhookUrl ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs bg-muted px-2 py-1.5 rounded border border-border truncate">
                {webhookUrl}
              </code>
              <button
                onClick={copyWebhookUrl}
                className="flex items-center justify-center size-8 rounded-md border border-border bg-muted hover:bg-accent transition-colors shrink-0"
                title="Copy URL"
              >
                {copiedUrl ? (
                  <Check size={14} className="text-[var(--success)]" />
                ) : (
                  <Copy size={14} className="text-muted-foreground" />
                )}
              </button>
            </div>
            {webhookPathUrl && (
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs bg-muted px-2 py-1.5 rounded border border-border truncate">
                  {webhookPathUrl}
                </code>
                <button
                  onClick={copyWebhookPathUrl}
                  className="flex items-center justify-center size-8 rounded-md border border-border bg-muted hover:bg-accent transition-colors shrink-0"
                  title="Copy custom path URL"
                >
                  {copiedPathUrl ? (
                    <Check size={14} className="text-[var(--success)]" />
                  ) : (
                    <Copy size={14} className="text-muted-foreground" />
                  )}
                </button>
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Save workflow to generate webhook URL
          </p>
        )}
        <p className="text-xs text-muted-foreground mt-2">
          Workflow must be <span className="font-medium text-[var(--success)]">active</span> to receive webhooks
        </p>
      </div>
    </div>
  );
}

// --- CurlImportExtension ---

function CurlImportExtension({ node }: NodeExtensionProps) {
  const [curlDialogOpen, setCurlDialogOpen] = useState(false);

  const handleImport = useCallback((parsed: ParsedCurlResult) => {
    const store = useWorkflowStore.getState();
    const currentNode = store.nodes.find((n) => n.id === node.id);
    if (!currentNode) return;
    store.updateNodeData(node.id, {
      parameters: {
        ...(currentNode.data as WorkflowNodeData).parameters,
        method: parsed.method,
        url: parsed.url,
        headers: parsed.headers,
        body: parsed.body,
      },
    });
  }, [node.id]);

  return (
    <>
      <div className="rounded-md border border-border bg-muted/30">
        <div className="flex items-center justify-between px-3 py-2">
          <div className="flex items-center gap-2">
            <Terminal size={14} className="text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">Import</span>
          </div>
          <button
            onClick={() => setCurlDialogOpen(true)}
            className="inline-flex h-7 items-center rounded-md border border-border bg-background px-2.5 text-xs font-medium text-foreground shadow-sm transition-colors hover:bg-accent"
          >
            Import cURL
          </button>
        </div>
      </div>
      <ImportCurlDialog
        open={curlDialogOpen}
        onOpenChange={setCurlDialogOpen}
        onImport={handleImport}
      />
    </>
  );
}
