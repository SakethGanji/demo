import { useUIModeStore } from '../../stores/uiModeStore';
import { Code } from 'lucide-react';

export function HTMLPanel() {
  const htmlContent = useUIModeStore((s) => s.htmlContent);

  return (
    <div className="flex flex-col h-full rounded-lg border bg-background">
      <div className="px-4 py-2 border-b flex items-center gap-2">
        <Code className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-medium text-foreground">Preview</h3>
      </div>
      <div className="flex-1 overflow-auto">
        {htmlContent ? (
          <iframe
            srcDoc={htmlContent}
            title="HTML Preview"
            className="w-full h-full border-0"
            sandbox="allow-scripts"
          />
        ) : (
          <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
            HTML output will appear here...
          </div>
        )}
      </div>
    </div>
  );
}
