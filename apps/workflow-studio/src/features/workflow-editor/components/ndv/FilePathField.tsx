/**
 * FilePathField - File path input with file browser dialog
 */

import { useState, useEffect, useId } from 'react';
import { Folder, File, ChevronRight, Home, ArrowUp, X, Search } from 'lucide-react';

interface FileEntry {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size?: number;
  extension?: string;
}

interface BrowseResponse {
  current_path: string;
  parent_path: string | null;
  entries: FileEntry[];
}

interface NodeProperty {
  displayName: string;
  name: string;
  required?: boolean;
  placeholder?: string;
  description?: string;
  typeOptions?: {
    extensions?: string;
  };
}

interface FilePathFieldProps {
  property: NodeProperty;
  value: string;
  onChange: (value: string) => void;
}

export function FilePathField({ property, value, onChange }: FilePathFieldProps) {
  const fieldId = useId();
  const [isOpen, setIsOpen] = useState(false);
  const [currentPath, setCurrentPath] = useState('~');
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchFilter, setSearchFilter] = useState('');

  const extensions = property.typeOptions?.extensions || '';

  const fetchDirectory = async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ path });
      if (extensions) {
        params.set('filter_extensions', extensions);
      }
      const response = await fetch(`/api/files/browse?${params}`);
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to browse directory');
      }
      const data: BrowseResponse = await response.json();
      setCurrentPath(data.current_path);
      setParentPath(data.parent_path);
      setEntries(data.entries);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load directory');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      // Start from the directory of current value, or home
      const startPath = value ? value.substring(0, value.lastIndexOf('/')) || '~' : '~';
      fetchDirectory(startPath);
    }
  }, [isOpen]);

  const handleEntryClick = (entry: FileEntry) => {
    if (entry.type === 'directory') {
      fetchDirectory(entry.path);
    } else {
      onChange(entry.path);
      setIsOpen(false);
    }
  };

  const handleGoUp = () => {
    if (parentPath) {
      fetchDirectory(parentPath);
    }
  };

  const handleGoHome = () => {
    fetchDirectory('~');
  };

  const formatSize = (bytes?: number) => {
    if (bytes === undefined) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const filteredEntries = entries.filter(
    (entry) =>
      entry.name.toLowerCase().includes(searchFilter.toLowerCase())
  );

  return (
    <div>
      <label htmlFor={fieldId} className="mb-1 block text-xs font-medium text-foreground/80">
        {property.displayName}
        {property.required && <span className="text-destructive ml-0.5">*</span>}
      </label>

      <div className="flex gap-1.5">
        <input
          id={fieldId}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={property.placeholder}
          className="flex-1 rounded border border-border/60 bg-background px-2.5 py-1.5 text-[13px] focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring/50"
        />
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="px-2.5 py-1.5 rounded border border-border/60 bg-muted/50 hover:bg-accent text-[12px] font-medium flex items-center gap-1"
        >
          <Folder size={13} />
          Browse
        </button>
      </div>

      {property.description && (
        <p className="mt-1 text-[11px] text-muted-foreground/70">{property.description}</p>
      )}

      {/* File Browser Modal */}
      {isOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50">
          <div className="bg-background border border-border/50 rounded-xl shadow-2xl w-[600px] max-h-[80vh] flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/50">
              <h3 className="text-[13px] font-medium text-foreground">Select File</h3>
              <button
                onClick={() => setIsOpen(false)}
                className="p-1 hover:bg-accent rounded"
              >
                <X size={18} />
              </button>
            </div>

            {/* Navigation bar */}
            <div className="flex items-center gap-2 px-4 py-1.5 border-b border-border/40 bg-muted/30">
              <button
                onClick={handleGoHome}
                className="p-1.5 hover:bg-accent rounded"
                title="Go to home directory"
              >
                <Home size={16} />
              </button>
              <button
                onClick={handleGoUp}
                disabled={!parentPath}
                className="p-1.5 hover:bg-accent rounded disabled:opacity-50 disabled:cursor-not-allowed"
                title="Go to parent directory"
              >
                <ArrowUp size={16} />
              </button>
              <div className="flex-1 px-2 py-1 bg-background rounded border border-border/50 text-[13px] truncate">
                {currentPath}
              </div>
            </div>

            {/* Search */}
            <div className="px-4 py-1.5 border-b border-border/40">
              <div className="relative">
                <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="text"
                  value={searchFilter}
                  onChange={(e) => setSearchFilter(e.target.value)}
                  placeholder="Filter files..."
                  className="w-full pl-8 pr-3 py-1.5 text-[13px] rounded border border-border/60 bg-background focus:outline-none focus:ring-1 focus:ring-ring/50"
                />
              </div>
            </div>

            {/* File list */}
            <div className="flex-1 overflow-auto p-2">
              {loading ? (
                <div className="flex items-center justify-center py-8 text-muted-foreground">
                  Loading...
                </div>
              ) : error ? (
                <div className="flex items-center justify-center py-8 text-destructive">
                  {error}
                </div>
              ) : filteredEntries.length === 0 ? (
                <div className="flex items-center justify-center py-8 text-muted-foreground">
                  {searchFilter ? 'No matching files' : 'No files found'}
                </div>
              ) : (
                <div className="space-y-0.5">
                  {filteredEntries.map((entry) => (
                    <button
                      key={entry.path}
                      onClick={() => handleEntryClick(entry)}
                      className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded hover:bg-accent text-left group"
                    >
                      {entry.type === 'directory' ? (
                        <Folder size={18} className="text-primary flex-shrink-0" />
                      ) : (
                        <File size={18} className="text-muted-foreground flex-shrink-0" />
                      )}
                      <span className="flex-1 truncate text-[13px]">{entry.name}</span>
                      {entry.type === 'directory' ? (
                        <ChevronRight size={16} className="text-muted-foreground opacity-0 group-hover:opacity-100" />
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          {formatSize(entry.size)}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="flex items-center justify-between px-4 py-2.5 border-t border-border/40 bg-muted/30">
              <span className="text-xs text-muted-foreground">
                {extensions && `Showing: ${extensions}`}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setIsOpen(false)}
                  className="px-3 py-1 text-[13px] rounded border border-border/60 hover:bg-accent"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
