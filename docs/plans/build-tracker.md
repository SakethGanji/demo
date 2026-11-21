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
| P07 | D | done (U2: send __row_id in set_cells/delete_rows) | session_018upNdQzFyc3sTFn8F8WBVu | 858200d |
| P08 | D | done | session_018upNdRRN6S5Y3rTnnk29BM | 656ab64 |
| P09 | D | done (split_files read-only for P10) | session_018upNdSNHpt3EuRkTqNbK1w | 7da74bb |
| X1 | ⟂ | done: 2 critical, 6 major → F-X1 | session_018upNdQEzTw3dUV29ygL2YH | 6822049 |
| F-X1 | D | started (blocks P10) | | |
| P11 | D2 | started | session_018upNdQkfQxUCUaQxHcgsFb | |
| P19 | G | done (mode audit) | session_018upNdPhxRCU1Z9oW9qWLpN | f9cbfe7 |
| P20 | G | started | | |
| P22 | O | done | session_018upNdPj3uWNkfz67KZVANF | b22d64d |
| U1 | U | done | session_018upNdQpQQ3Pdy7XWP9nE4H | a9eed11 |
| P18 | G | done (note: U1 must send team_id on New project) | session_018upNdRkZyzaZ19U6LU2cj7 | cbaffa2 |

## Open notes carried forward
- **10M edit commit takes 99 s** (P07 path). Full pandas reads remain in data-doctor (and its apply), the edit preview and Feature Lab. X1 to judge whether this is a fix before P10.
- **U lane:** switch the UI's GET /datasets to ?limit=50&after= (the full list takes 1.15 s at 20k versions). Send __row_id in set_cells/delete_rows.
- **P23:** carry ix_datasets_list into the Postgres DDL.
- **P20:** mask diff samples.
- **Still on in-process threads (not on the queue):** stats profile, annotations, shift, Data Doctor label job, Autopilot. Move them in P23 or a follow-up.
- **U lane:** send project_id in the /uploads query (it authorises from the query). Grey out "New team" via /authz/check unless self_service_teams.
- **P23:** keep audit_log.details/decision as TEXT (not jsonb), copy prev_hash/row_hash/audit_head verbatim, and use FOR UPDATE on the head row.
- **X4:** rewire the four-eyes decide paths to policy.decide_check (they use governance.same_person today).
