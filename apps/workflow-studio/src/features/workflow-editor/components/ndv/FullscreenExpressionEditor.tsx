import { useState, useRef, useEffect } from 'react';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/shared/components/ui/alert-dialog';
import { Button } from '@/shared/components/ui/button';
import { Textarea } from '@/shared/components/ui/textarea';

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

        <Textarea
          ref={textareaRef}
          value={localValue}
          onChange={(e) => setLocalValue(e.target.value)}
          placeholder={placeholder}
          className="flex-1 font-mono resize-none"
          data-testid="expr-fullscreen-input"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              handleDone();
            }
          }}
        />

        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <Button onClick={handleDone} data-testid="expr-fullscreen-done">
            Done
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
