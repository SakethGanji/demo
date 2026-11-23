// One Autopilot run as a list of steps: what the live view draws on its iteration string.

export type Metrics = { f1: number; rec: number; lat: number }

export type Diff = { removed: number; relabelled: number; synthetic: number }

export type Step = {
  id: string
  kind: 'step' | 'branch' | 'ask'
  short: string
  title: string
  after?: Metrics            // metrics once this step is kept
  tried?: Metrics            // metrics of a dropped branch
  diff?: Partial<Diff>
  // ask only
  answer?: string
  auto?: boolean             // answered by Vela, not by you
}
