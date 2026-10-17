import { Keyboard } from 'lucide-react';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/shared/components/ui/sheet';
import { Kbd } from '@/shared/components/ui/kbd';

interface Shortcut {
  key: string;
  description: string;
}

interface KeyboardShortcutsHelpProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  shortcuts: readonly Shortcut[];
}

export function KeyboardShortcutsHelp({
  open,
  onOpenChange,
  shortcuts,
}: KeyboardShortcutsHelpProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[340px] sm:w-[400px]">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Keyboard size={18} />
            Keyboard Shortcuts
          </SheetTitle>
          <SheetDescription>
            Quick actions to speed up your workflow
          </SheetDescription>
        </SheetHeader>
        <div className="mt-4 space-y-1">
          {shortcuts.map((shortcut) => (
            <div
              key={shortcut.key}
              className="flex items-center justify-between py-2 px-2 rounded-ctl row-hover"
            >
              <span className="text-sm text-muted-foreground">
                {shortcut.description}
              </span>
              <Kbd className="h-auto px-2 py-1 font-mono">
                {shortcut.key}
              </Kbd>
            </div>
          ))}
        </div>
      </SheetContent>
    </Sheet>
  );
}
