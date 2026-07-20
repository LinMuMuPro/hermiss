"""Cron scheduling helpers for proactive check-ins."""

import json
import subprocess
from datetime import timedelta


def schedule_checkin_jobs(
    *,
    profile: str,
    deliver_target: str,
    checkin_file,
    created_at,
    effective_delay_minutes: int,
    next_followup_minutes,
    stage_prompt_builder,
    extract_cron_job_id,
) -> list[str]:
    job_ids = []
    job_specs = [(0, effective_delay_minutes)]
    cumulative_minutes = effective_delay_minutes
    for stage in range(1, 4):
        cumulative_minutes += next_followup_minutes(stage - 1)
        job_specs.append((stage, cumulative_minutes))

    for stage, delay_minutes in job_specs:
        delay_text = f"{max(1, int(delay_minutes))}m"
        stage_fire_at = created_at + timedelta(minutes=delay_minutes)
        result = subprocess.run(
            [
                "hermes", "--profile", profile, "cron", "create",
                delay_text, stage_prompt_builder(stage, stage_fire_at, delay_minutes),
                "--deliver", deliver_target,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            job_id = extract_cron_job_id(result.stdout)
            job_ids.append(job_id)
            print(
                f"[message-analyzer] Check-in cron stage {stage}: {delay_text} "
                f"(job={job_id})"
            )
        else:
            err = result.stderr.strip()
            print(f"[message-analyzer] Check-in cron failed stage {stage} (rc={result.returncode}): {err}")
            break

    if job_ids:
        data = json.loads(checkin_file.read_text())
        data["job_id"] = job_ids[0]
        data["job_ids"] = job_ids
        checkin_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return job_ids


def dispatch_reminders(
    *,
    reminder_dir,
    profile: str,
    deliver: str,
    get_undispatched_reminders,
    mark_reminder_dispatched,
    compute_delay,
    extract_cron_job_id,
) -> None:
    try:
        pending = get_undispatched_reminders(reminder_dir)
    except Exception:
        return

    for item in pending:
        fire_at = item.get("fire_at", "")
        reminder_text = item.get("reminder_text", "")
        delay = compute_delay(fire_at)
        if not delay:
            print(f"[message-analyzer] Reminder in the past, discarding: {reminder_text}")
            mark_reminder_dispatched(reminder_dir, fire_at, reminder_text)
            continue

        prompt = (
            f"[HERMES REMINDER] {reminder_text}\n\n"
            "The user asked you to remind them about this. "
            "Bring it up warmly and naturally."
        )
        try:
            result = subprocess.run(
                ["hermes", "--profile", profile, "cron", "create", delay, prompt, "--deliver", deliver],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                extract_cron_job_id(result.stdout)
                mark_reminder_dispatched(reminder_dir, fire_at, reminder_text)
                print(f"[message-analyzer] Reminder cron: {reminder_text} (in {delay})")
            else:
                err = result.stderr.strip()
                print(f"[message-analyzer] Reminder cron failed (rc={result.returncode}): {err}")
        except FileNotFoundError:
            print("[message-analyzer] hermes CLI not on PATH — cannot schedule reminders")
            return
        except Exception as e:
            print(f"[message-analyzer] Reminder cron error: {e}")
