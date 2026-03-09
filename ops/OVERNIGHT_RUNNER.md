# Overnight Runner (Detached Jobs)

Purpose: run long tasks in the background so work keeps running even if the UI/session disconnects.

## Scripts

- `scripts/detached_job_start.sh`
- `scripts/detached_job_status.sh`
- `scripts/detached_job_stop.sh`
- `scripts/detached_job_checkpoint.sh`

## Usage

Start a detached job:

```bash
./scripts/detached_job_start.sh research_backlog "bash -lc 'echo start; sleep 5; echo done'"
```

Check job status and recent logs:

```bash
./scripts/detached_job_status.sh research_backlog
```

Write a checkpoint line from any running command:

```bash
./scripts/detached_job_checkpoint.sh research_backlog "completed phase 0 task 1"
```

Stop job:

```bash
./scripts/detached_job_stop.sh research_backlog
```

Force stop if needed:

```bash
./scripts/detached_job_stop.sh research_backlog --force
```

## Files created per job

- `.runtime/detached_jobs/<job_name>/pid`
- `.runtime/detached_jobs/<job_name>/command.txt`
- `.runtime/detached_jobs/<job_name>/started_at_utc.txt`
- `.runtime/detached_jobs/<job_name>/stdout.log`
- `.runtime/detached_jobs/<job_name>/stderr.log`
- `.runtime/detached_jobs/<job_name>/checkpoint.log` (when used)

## Planned build launch command

When you give `go`, we will start one detached job for the backlog build and checkpoint progress continuously.
