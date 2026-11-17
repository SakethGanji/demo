/** A parameter with its symbol: `C = 1.0`. */
export function ParamChip({ sym, value }: { sym: string; value: string }) {
  return (
    <span className="chip"><span className="sym">{sym}</span>=<span>{value}</span></span>
  )
}
