# HANDOVER.md

Överlämning mellan agenter och sessioner. Läs innan du börjar, uppdatera innan du slutar.

## Aktuellt läge

*Senast inventerat 2026-11-04 av Claude (Cowork).*

Projekt: På en Linux-testdator med systempaketen installerade:

- Huvudbranch: `main`
- Senaste commit på `main`: 2026-11-01 `210abf4` Add privacy guard config, hook, and CI scan (#7)
- Öppna PR:er: 1
- Andra brancher på remote: 7

## Pågående arbete

| Agent/vem | Branch | Status | Beskrivning |
|-----------|--------|--------|-------------|
| jojo-swe | `feature/swap-experiment` | PR #4 öppen, uppdaterad 2026-10-26 | 0.11: borrower block export and swap experiment under real memory pressure |
| Cursor | `cursor/privacy-guard-a730` | branch utan PR, senaste commit 2026-11-01 | Add privacy guard config, hook, and CI scan |
| Cursor | `cursor/mask-lan-and-home-paths-1e84` | branch utan PR, senaste commit 2026-11-01 | docs: mask private LAN address and home-directory usernames |
| Claude | `feature/swap-experiment-block-export` | branch utan PR, senaste commit 2026-10-26 | chore: recall session state |
| okänd (människa/agent) | `feature/installed-cli` | branch utan PR, senaste commit 2026-10-25 | Package all commands and NBD plugin for standalone installation |
| okänd (människa/agent) | `feature/cache-integrity` | branch utan PR, senaste commit 2026-10-25 | Verify remote cache blocks before exposing data |
| okänd (människa/agent) | `feature/persistent-checkpoints` | branch utan PR, senaste commit 2026-10-25 | Add persistent atomic checkpoints and process crash recovery tests |

## Nästa steg

- Granska och besluta om de öppna PR:erna ovan (merga eller stäng).
- Gå igenom brancher utan PR: öppna PR, merga eller radera (fråga användaren innan radering).
- Fyll på "Bygga och testa" i AGENTS.md om något saknas eller är fel.

## Kända problem

- Inga dokumenterade ännu.

## Senaste commits

- 2026-11-01 `210abf4` Add privacy guard config, hook, and CI scan (#7)
- 2026-11-01 `e9f4e4f` Merge pull request #6 from jojo-swe/cursor/mask-lan-and-home-paths-1e84
- 2026-11-01 `e42babd` docs: mask private LAN address and home-directory usernames
- 2026-10-28 `653fe06` Merge pull request #5 from jojo-swe/feature/swap-experiment-block-export
- 2026-10-26 `a179cf4` chore: recall session state
- 2026-10-26 `1d04556` docs: corrections from conclusions review (disclose mid-run code change, exact commands, K4/K5 wording, caveats on emulated crossover, saved microbenchmark raw data); record VmLck peaks
- 2026-10-26 `5304552` results: swap experiment raw data, aggregated tables, conclusions, status update; swap-report command; K5 restart test
- 2026-10-26 `4234b8c` data: uniform experiment repetitions 2-3 raw results
- 2026-10-26 `a804c56` review fixes: no cached-read stall behind mirror writes, lock before donor use, worker mlockall, lab metric/pipe/fork hardening, stronger tests
- 2026-10-26 `3c81efa` fix(swap-lab): unique mountpoint per run, record setup errors, order offset; keep part-1 raw data

## Logg

| Datum | Agent | Vad som gjordes |
|-------|-------|-----------------|
| 2026-11-04 | Claude (Cowork) | Lade till AGENTS.md, HANDOVER.md och CLAUDE.md; inventerade PR:er, brancher och commits. |
