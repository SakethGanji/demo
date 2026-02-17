import { useUIModeStore } from '../../stores/uiModeStore';
import DataTable from '../ndv/DataTable';

export function TablePanel() {
  const tableData = useUIModeStore((s) => s.tableData);

  if (!tableData || tableData.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-xs">
        No table data to display
      </div>
    );
  }

  return (
    <div className="p-2">
      <DataTable data={tableData} />
    </div>
  );
}
