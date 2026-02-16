import { useMemo } from 'react';
import { useUIModeStore } from '../../stores/uiModeStore';
import { FileText } from 'lucide-react';

export function PDFPanel() {
  const pdfBase64 = useUIModeStore((s) => s.pdfBase64);

  const blobUrl = useMemo(() => {
    if (!pdfBase64) return null;
    const byteChars = atob(pdfBase64);
    const byteNumbers = new Uint8Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) byteNumbers[i] = byteChars.charCodeAt(i);
    const blob = new Blob([byteNumbers], { type: 'application/pdf' });
    return URL.createObjectURL(blob);
  }, [pdfBase64]);

  return (
    <div className="flex flex-col h-full rounded-lg border bg-background">
      <div className="px-4 py-2 border-b flex items-center gap-2">
        <FileText className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-medium">PDF Preview</h3>
      </div>
      <div className="flex-1 overflow-auto">
        {blobUrl ? (
          <iframe
            src={blobUrl}
            title="PDF Preview"
            className="w-full h-full border-0"
          />
        ) : (
          <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
            PDF output will appear here...
          </div>
        )}
      </div>
    </div>
  );
}
