import { memo, useState, useRef, useEffect } from 'react';
import { type NodeProps, type Node, NodeResizer } from '@xyflow/react';
import { Trash2 } from 'lucide-react';
import { useWorkflowStore } from '../../../stores/workflowStore';
import type { StickyNoteData } from '../../../types/workflow';

const colorClasses: Record<string, { bg: string; text: string; resizer: string }> = {
  yellow: {
    bg: 'bg-[var(--sticky-yellow-bg)]',
    text: 'text-[var(--sticky-yellow-text)]',
    resizer: 'var(--sticky-yellow-resizer)',
  },
  blue: {
    bg: 'bg-[var(--sticky-blue-bg)]',
    text: 'text-[var(--sticky-blue-text)]',
    resizer: 'var(--sticky-blue-resizer)',
  },
  green: {
    bg: 'bg-[var(--sticky-green-bg)]',
    text: 'text-[var(--sticky-green-text)]',
    resizer: 'var(--sticky-green-resizer)',
  },
  pink: {
    bg: 'bg-[var(--sticky-pink-bg)]',
    text: 'text-[var(--sticky-pink-text)]',
    resizer: 'var(--sticky-pink-resizer)',
  },
  purple: {
    bg: 'bg-[var(--sticky-purple-bg)]',
    text: 'text-[var(--sticky-purple-text)]',
    resizer: 'var(--sticky-purple-resizer)',
  },
};

function StickyNote({ id, data, selected }: NodeProps<Node<StickyNoteData>>) {
  const [isEditing, setIsEditing] = useState(false);
  const [content, setContent] = useState(data.content || '');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const updateStickyNote = useWorkflowStore((s) => s.updateStickyNote);
  const deleteNode = useWorkflowStore((s) => s.deleteNode);

  const stickyColor = colorClasses[data.color || 'yellow'] ? (data.color || 'yellow') : 'yellow';
  const colors = colorClasses[stickyColor];
  const width = data.width || 200;
  const height = data.height || 150;

  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus();
      textareaRef.current.select();
    }
  }, [isEditing]);

  const handleBlur = () => {
    setIsEditing(false);
    updateStickyNote(id, { content });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setIsEditing(false);
      setContent(data.content || '');
    }
  };

  const handleColorChange = (color: StickyNoteData['color']) => {
    updateStickyNote(id, { color });
  };

  const handleResize = (_: unknown, params: { width: number; height: number }) => {
    updateStickyNote(id, { width: params.width, height: params.height });
  };

  return (
    <div
      className={`
        relative rounded-panel
        ${colors.bg}
      `}
      style={{
        width,
        height,
        boxShadow: selected
          ? `0 0 0 1px var(--ring), 0 0 0 4px color-mix(in srgb, var(--ring) 16%, transparent), var(--node-shadow)`
          : `inset 0 0 0 1px var(--sticky-${stickyColor}-border), var(--node-shadow)`,
      }}
    >
      <NodeResizer
        isVisible={selected}
        minWidth={150}
        minHeight={100}
        handleStyle={{
          width: 8,
          height: 8,
          borderRadius: 4,
          backgroundColor: colors.resizer,
          border: 'none',
        }}
        lineStyle={{
          borderColor: colors.resizer,
          borderWidth: 1,
        }}
        onResize={handleResize}
      />
      {/* Color picker - show on selection */}
      {selected && (
        <div className="glass-overlay absolute -top-10 left-0 flex gap-1 p-1">
          {Object.keys(colorClasses).map((color) => (
            <button
              key={color}
              onClick={() => handleColorChange(color as StickyNoteData['color'])}
              className={`
                w-5 h-5 rounded-full transition-transform
                ${colorClasses[color].bg}
                ${data.color === color ? 'scale-110' : 'hover:scale-105'}
              `}
              style={{ boxShadow: data.color === color ? '0 0 0 2px var(--ring)' : `inset 0 0 0 1px var(--sticky-${color}-border)` }}
              aria-label={`${color} note`}
            />
          ))}
          <div className="w-px h-5 bg-hairline mx-1 self-center" />
          <button
            onClick={() => deleteNode(id)}
            className="p-0.5 rounded-chip hover:bg-destructive-soft text-destructive"
            title="Delete note"
          >
            <Trash2 size={14} />
          </button>
        </div>
      )}

      {/* Content */}
      {isEditing ? (
        <textarea
          ref={textareaRef}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          className={`
            nodrag w-full h-full p-3 bg-transparent resize-none
            ${colors.text} placeholder:text-current placeholder:opacity-50
            focus:outline-none
          `}
          placeholder="Add a note..."
        />
      ) : (
        <div
          onDoubleClick={() => setIsEditing(true)}
          className={`
            w-full h-full p-3 cursor-text whitespace-pre-wrap overflow-auto
            ${colors.text}
            ${!content ? 'opacity-50' : ''}
          `}
        >
          {content || 'Double-click to edit...'}
        </div>
      )}
    </div>
  );
}

export default memo(StickyNote);
