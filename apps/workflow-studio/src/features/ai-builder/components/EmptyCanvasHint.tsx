import { AnimatePresence, motion } from 'motion/react';
import { ArrowRight } from 'lucide-react';
import { dur, ease } from '@/shared/motion/tokens';
import { useEditorLayoutStore } from '@/features/workflow-editor/stores/editorLayoutStore';
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore';
import { useStageStore } from '../stage/stageStore';

/** On an empty new workflow with the Build tab open, point at where to describe it. */
export function EmptyCanvasHint() {
  const isNew = useWorkflowStore((s) => !s.workflowId);
  const empty = useWorkflowStore((s) => !s.nodes.some((n) => n.type !== 'addNodes'));
  const buildOpen = useEditorLayoutStore((s) => s.rightPanelOpen && s.rightPanelTab === 'builder');
  const staged = useStageStore((s) => s.active);
  const show = isNew && empty && buildOpen && !staged;
  return (
    <AnimatePresence>
      {show && (
        <motion.div
          key="hint"
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          transition={{ duration: dur.slow, ease: ease.out, delay: 0.15 }}
          className="glass-overlay pointer-events-none absolute left-1/2 top-4 z-10 flex -translate-x-1/2 items-center gap-1.5 px-3 py-1.5 text-[13px] text-ai-text"
          data-testid="ai-empty-hint"
        >
          Describe it in the Build panel <ArrowRight className="size-3.5" />
        </motion.div>
      )}
    </AnimatePresence>
  );
}
