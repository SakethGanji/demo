import { useState } from 'react';
import { Check, Loader2, Play, Plus, Sparkles } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Badge } from '@/shared/components/ui/badge';
import { Input } from '@/shared/components/ui/input';
import { Switch } from '@/shared/components/ui/switch';
import { useTestStore } from '../../stores/testStore';
import { runFuzz, saveFindingAsTest } from '../../lib/testAiActions';
import { openNodeByName } from '../../lib/testActions';
import { findingField, inputPreview, type Finding } from '../../lib/testsAi';
import { useNodeLabels } from '../../hooks/useNodeLabels';

const KIND_LABEL: Record<Finding['kind'], string> = { node_error: 'Step failed', contract: 'Contract broken', crash: 'Crashed' };

function FindingRow({ f }: { f: Finding }) {
  const saved = useTestStore((s) => s.savedFindings.includes(f.id));
  const labels = useNodeLabels();
  const field = findingField(f);
  return (
    <div
      className="flex flex-col gap-0.5 rounded-ctl px-2 py-1.5 text-[12.5px] hover:bg-(--hover-fill)"
      data-testid="fuzz-finding"
      data-kind={f.kind}
      data-node={f.node ?? ''}
    >
      <div className="flex items-center gap-2">
        <Badge variant="error" className="shrink-0">{KIND_LABEL[f.kind]}</Badge>
        {f.node ? (
          <button type="button" className="min-w-0 truncate font-medium text-foreground hover:underline" onClick={() => openNodeByName(f.node!)} data-testid="fuzz-node">
            {labels[f.node] ?? f.node}
          </button>
        ) : (
          <span className="text-muted-foreground">Whole workflow</span>
        )}
        {field && <span className="shrink-0 font-mono text-[11.5px] text-muted-foreground" data-testid="fuzz-field">{field}</span>}
        {f.count > 1 && <span className="shrink-0 text-[11.5px] text-faint">{f.count} inputs</span>}
        <span className="flex-1" />
        <Button variant="outline" size="xs" disabled={saved} onClick={() => saveFindingAsTest(f)} data-testid="fuzz-save">
          {saved ? <Check /> : <Plus />} {saved ? 'Saved' : 'Save as test'}
        </Button>
      </div>
      <div className="truncate font-mono text-[11.5px] text-muted-foreground" title={JSON.stringify(f.input.items[0] ?? {})} data-testid="fuzz-input">
        Input: {inputPreview(f)}
      </div>
      <div className="text-[12px] text-destructive" data-testid="fuzz-error">{f.error.message}</div>
    </div>
  );
}

/** Variations: try many odd inputs and list what breaks. A finding saves as a draft test that fails until fixed. */
export default function FuzzPanel() {
  const running = useTestStore((s) => s.fuzzRunning);
  const result = useTestStore((s) => s.fuzzResult);
  const [count, setCount] = useState('60');
  const [ai, setAi] = useState(false);
  const n = Math.min(200, Math.max(1, Math.round(Number(count)) || 60));

  return (
    <div className="flex flex-col gap-2 p-2" data-testid="fuzz-panel">
      <div className="flex flex-wrap items-center gap-3 text-[12.5px]">
        <label className="flex items-center gap-2 text-muted-foreground">
          Inputs to try
          <Input
            type="number"
            min={1}
            max={200}
            value={count}
            onChange={(e) => setCount(e.target.value)}
            className="h-7 w-20"
            data-testid="fuzz-count"
            aria-label="Number of inputs to try"
          />
        </label>
        <label className="flex items-center gap-2 text-muted-foreground">
          <Switch checked={ai} onCheckedChange={setAi} data-testid="fuzz-ai" aria-label="Also ask the AI for inputs" />
          <Sparkles size={12} /> Also ask the AI
        </label>
        <Button size="sm" onClick={() => void runFuzz({ count: n, ai })} disabled={running} data-testid="fuzz-run">
          {running ? <Loader2 className="animate-spin" /> : <Play />} Try variations
        </Button>
        {result && !running && (
          <span className="text-[12px] text-muted-foreground" data-testid="fuzz-summary">
            {result.findings.length === 0
              ? `No problems in ${result.inputs_tried} inputs`
              : `${result.findings.length} ${result.findings.length === 1 ? 'problem' : 'problems'} in ${result.inputs_tried} inputs`}
            {result.ai_inputs > 0 && ` (${result.ai_inputs} from the AI)`}
          </span>
        )}
      </div>
      {result?.findings.map((f) => <FindingRow key={f.id} f={f} />)}
    </div>
  );
}
