export type Tool = { id: string; name: string; verb: string; desc: string; meta: string; route: string }

/** The toolkit. Every tool is a verb; the workbench, toolbelt and command palette all read this list. */
export const TOOLS: Tool[] = [
  { id: 'quick', name: 'Quick Train', verb: 'Run', desc: 'Give it a dataset, pick a recipe, and watch every classifier settle.', meta: 'TF-IDF → LogReg (OvR) · XGBoost', route: 'workflow' },
  { id: 'autopilot', name: 'Vela Autopilot', verb: 'Delegate', desc: 'Give Vela a dataset and a goal. It trains and fixes the data, and only asks you what is true.', meta: 'asks on ground truth only', route: 'autopilot' },
  { id: 'train', name: 'Training Gym', verb: 'Train', desc: 'String a pipeline end to end and fit it, step by step.', meta: 'step 07 of 10 · Draft', route: 'gym' },
  { id: 'compare', name: 'String Plot', verb: 'Compare', desc: 'Thread every run across its parameters and metrics; brush to filter.', meta: '9 runs · 3 selected', route: 'experiments' },
  { id: 'register', name: 'Model Registry', verb: 'Register', desc: 'Promote a run to a version and trace its lineage to production.', meta: 'v3.0.0-rc1 awaiting approval', route: 'registry' },
  { id: 'vela', name: 'Vela', verb: 'Ask', desc: 'Get a finding with a number and one patch you can apply.', meta: '2 patches ready', route: 'vela' },
]
