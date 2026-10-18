import { Trash2 } from 'lucide-react';
import { Textarea } from '@/shared/components/ui/textarea';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Tip } from '@/shared/components/ui/tooltip';
import type { SpecIssue, SpecRule as Rule } from '../types';
import { IssueList } from './SpecIssues';

export function SpecRule({ rule, testCount, issues, readOnly, onChange, onRemove }: {
  rule: Rule;
  /** how many tests check this rule */
  testCount: number;
  issues: SpecIssue[];
  readOnly?: boolean;
  onChange: (text: string) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex flex-col gap-1 py-1.5" data-testid={`ai-spec-rule-${rule.id}`}>
      <div className="flex items-start gap-2">
        <Textarea
          value={rule.text}
          rows={2}
          readOnly={readOnly}
          aria-label={`Rule ${rule.id}`}
          className="min-h-0 flex-1 resize-none"
          onChange={(e) => onChange(e.target.value)}
          data-testid={`ai-spec-rule-text-${rule.id}`}
        />
        <div className="flex shrink-0 flex-col items-end gap-1 pt-0.5">
          <Badge variant={testCount ? 'neutral' : 'warning'}>{testCount ? `${testCount} ${testCount === 1 ? 'test' : 'tests'}` : 'No test'}</Badge>
          {!readOnly && (
            <Tip label="Remove this rule">
              <Button variant="ghost" size="icon-sm" onClick={onRemove} aria-label={`Remove rule ${rule.id}`}><Trash2 /></Button>
            </Tip>
          )}
        </div>
      </div>
      <IssueList issues={issues} />
    </div>
  );
}
