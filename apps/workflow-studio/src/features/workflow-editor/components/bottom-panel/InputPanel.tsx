import { Play } from 'lucide-react';
import CodeEditor from '@/shared/components/ui/code-editor';
import { useExecutionStream } from '../../hooks/useExecutionStream';
import { useEditorLayoutStore } from '../../stores/editorLayoutStore';

export function InputPanel() {
  const payloadInput = useEditorLayoutStore((s) => s.payloadInput);
  const setPayloadInput = useEditorLayoutStore((s) => s.setPayloadInput);
  const { executeWorkflow } = useExecutionStream();

  const handleRunWithPayload = () => {
    try {
      const parsed = JSON.parse(payloadInput);
      executeWorkflow(parsed);
    } catch {
      executeWorkflow({});
    }
  };

  const handleRunWithoutPayload = () => {
    executeWorkflow({});
  };

  return (
    <div className="h-full relative">
      <CodeEditor
        value={payloadInput}
        onChange={setPayloadInput}
        language="json"
        height="100%"
      />
      <div className="absolute bottom-3 right-3 flex items-center gap-2">
        <button
          onClick={handleRunWithoutPayload}
          className="h-7 px-3 rounded-md border border-border bg-card text-[12px] font-medium text-muted-foreground hover:text-foreground hover:bg-accent flex items-center gap-1.5"
        >
          <Play size={11} />
          Run without Payload
        </button>
        <button
          onClick={handleRunWithPayload}
          className="h-7 px-3 rounded-md bg-[var(--success)] text-white text-[12px] font-medium hover:brightness-110 flex items-center gap-1.5"
        >
          <Play size={11} fill="currentColor" />
          Run with Payload
        </button>
      </div>
    </div>
  );
}
