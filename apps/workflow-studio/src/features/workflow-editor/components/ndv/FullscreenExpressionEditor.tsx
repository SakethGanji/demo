import { useState, useRef, useEffect } from 'react';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/shared/components/ui/alert-dialog';

interface FullscreenExpressionEditorProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: string;
  onChange: (value: string) => void;
  label?: string;
  placeholder?: string;
}

export default function FullscreenExpressionEditor({
  open,
  onOpenChange,
  value,
  onChange,
  label,
  placeholder,
}: FullscreenExpressionEditorProps) {
  const [localValue, setLocalValue] = useState(value);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Sync local value & focus when opening
  useEffect(() => {
    if (open) {
      setLocalValue(value);
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }, [open, value]);

  const handleDone = () => {
    onChange(localValue);
    onOpenChange(false);
  };

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="w-[calc(100vw-8rem)] max-w-none h-[calc(100vh-8rem)] flex flex-col">
        <AlertDialogHeader>
          <AlertDialogTitle>{label || 'Edit value'}</AlertDialogTitle>
        </AlertDialogHeader>

        <textarea
          ref={textareaRef}
          value={localValue}
          onChange={(e) => setLocalValue(e.target.value)}
          placeholder={placeholder}
          className="flex-1 w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring resize-none"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              handleDone();
            }
          }}
        />

        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <button
            onClick={handleDone}
            className="inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            Done
          </button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
