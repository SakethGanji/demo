# Data platform build tracker

**Plan:** docs/plans/data-platform-final.md. §9.1 is the phase list; §11 holds the user decisions.

**Orchestrator:** session_018upNdRigdGd9A2nGzFSQXq.

## Rules for every phase session

- Implement exactly the phase's row in §9.1 (goal, files, verification chain, done-when), plus the §11 decisions.
- **Verify** with manual API calls chained the way the UI calls them. No smoke or integration suites. Add `npm run build` when the UI changes.
- Write **docs/progress/<phase>.md** with the calls and their outputs.
- **Git:** run `git pull` (merge, never rebase or force) before every push. Push only to motion-components. No PRs. Keep commits small.
- **Commit message trailer:**

  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_018upNdRigdGd9A2nGzFSQXq
  ```

- **Parallel work:** other lanes may run at the same time. Stay inside your phase's "Files touched". If you must touch a file another lane owns, keep the change minimal and say so in the report.
- **Long jobs:** stay in-turn and poll them. Don't end the turn waiting on a scheduled wakeup. Delete scratch predictors and seeds you made.
- **Report:** send_message to the orchestrator with the head commit, what was done, the verification results, any deviations, and anything that blocks dependants.

## Status

| Phase | Lane | Status | Session | Head |
|---|---|---|---|---|
| P01 | D | done | session_018upNdRLvgWS6ojVV61QiZu | 76b3dc6 |
| P03 | R | done | session_018upNdMfWmu4csFiRDB9cYA | 884e586 |
| P04 | R | done (grep gate 0) | session_018upNdQ4YR8uygzkhyFodYa | ee570d0 |
| P02 | D | done | session_018upNdMxLoGmBn2rfMZrStw | ab61873 |
| P05 | D | done | session_018upNdR5aiQ2E9Luej6fmJ6 | 112b4d0 |
| P06 | D | done | session_018upNdPmVFDA7q7c2wULNRM | eb59e9c |
| P07 | D | started | session_018upNdQzFyc3sTFn8F8WBVu | |
| U1 | U | done | session_018upNdQpQQ3Pdy7XWP9nE4H | a9eed11 |
| P18 | G | done (note: U1 must send team_id on New project) | session_018upNdRkZyzaZ19U6LU2cj7 | cbaffa2 |
