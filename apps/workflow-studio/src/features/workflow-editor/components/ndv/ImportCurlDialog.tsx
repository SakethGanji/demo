import { useState, useRef, useEffect } from 'react';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/shared/components/ui/alert-dialog';
import { parseCurl, type ParsedCurlResult } from '../../lib/parseCurl';

interface ImportCurlDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImport: (parsed: ParsedCurlResult) => void;
}

export default function ImportCurlDialog({ open, onOpenChange, onImport }: ImportCurlDialogProps) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-focus textarea on open, clear state
  useEffect(() => {
    if (open) {
      setValue('');
      setError('');
      // Small delay so the dialog renders before focusing
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }, [open]);

  const handleImport = () => {
    const trimmed = value.trim();
    if (!trimmed) {
      setError('Please paste a cURL command');
      return;
    }
    try {
      const parsed = parseCurl(trimmed);
      onImport(parsed);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to parse cURL command');
    }
  };

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Import from cURL</AlertDialogTitle>
          <AlertDialogDescription>
            Paste a cURL command to auto-populate method, URL, headers, and body.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <textarea
          ref={textareaRef}
          rows={8}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            if (error) setError('');
          }}
          placeholder={`curl -X POST https://api.example.com/data \\\n  -H "Authorization: Bearer token" \\\n  -H "Content-Type: application/json" \\\n  -d '{"key": "value"}'`}
          className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring resize-none"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              handleImport();
            }
          }}
        />

        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}

        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <button
            onClick={handleImport}
            className="inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
          >
            Import
          </button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
