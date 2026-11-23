# Command workspace concept

Open `index.html` directly in a browser. It is one portable HTML file with all CSS, JavaScript,
and SVG icons embedded; it has no external dependencies. No build, installation, network, credentials, or running
services are required. This is a visual and interactive design prototype, not production application code.

Suggested walkthrough:

1. Compare **Today’s structure** with **Proposed workspace** using the top switch.
2. Open **Data**; explore Datasets, Tables, and Connections.
3. Open **Reconciliation — October**. Switch workbook sheets and inspect schema, versions, and lineage.
4. Select **Build an app**, review the dataset-version choice, and open the proposed builder.
5. Inspect its backend concept, open an exception, and try the simulated publishing flow.
6. Explore Automations, Activity, and Settings. Toggle the theme using the bottom-left control.

Upload and new-version controls simulate processing entirely in the browser. They do not read or send
selected file contents. The download control creates a small synthetic CSV, not an original workbook.
Demo changes are session-only; reloading resets them. Metrics, status, activity, workflow steps and
schema quality numbers are illustrative. Dataset SDK code is proposed, not an implemented contract.

The existing production studio is unchanged. Other original mockups remain in `design-mockups/`.

Verified in Chromium with Playwright: navigation comparison, dataset search, sheet switching, versions,
lineage, build/publish dialogs, folder creation, simulated upload, command search, light/dark themes,
and mobile layout. Preview captures are local under `screenshots/command-vision/`.
