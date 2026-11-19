"""Run a measurement set unattended, and stop when it is actually finished.

    python scripts/run_set.py --stop-at 20 --until 13:00

Nothing here depends on an agent session staying alive; this process owns the
set. Run it detached, and it will stop the bot and close the game itself.

**A set ends on its run count, not on a clock.** `--stop-at` is the real
argument: `--until` exists only as a backstop for a bot that cannot make
progress at all. A deadline that fires mid-set leaves a partial that cannot be
compared with any other set, which is the whole purpose of running sets.

Every 60 seconds it checks the bot process (through the project's own
`bot_control.ps1 -Action status`), the mod API on localhost:15526, and whether
the decision log is still growing. If the bot has died, the API has stopped
answering, or the log has been static for STUCK_MINUTES, it force-closes Slay
the Spire 2, relaunches it through Steam and restarts the bot. The user
authorised that explicitly.

At each set boundary (a multiple of --set-size runs in runs.jsonl) it stops the
bot and closes the game for --break-minutes, to let the machine cool, then
starts again.

At --stop-at runs, or failing that at --until, it stops the bot, closes the
game and optionally runs a --report-script. Its own log is `logs/run_set.log`.

**The API goes down for about two minutes during a relaunch**, and reading
that as a crash is how the 2026-11-13 stall lasted 3h35m instead of four
minutes: this supervisor restarted the bot process each time, which zeroed the
bot's own `_consecutive_recoveries`, so the bot's escalation to abandon the run
never fired -- the counter read 1 on all sixty passes. Hence API_DOWN_POLLS:
API silence has to persist across several polls before it counts as a crash.
Two supervisors that each undo the other make no progress at all.

It touches nothing outside the bot: the project folder, the SlayTheSpire2
process, and Steam's launch of that one app id.

Restarts use a plain `start`, never `-KeepRun`, so a run interrupted by a
restart is abandoned rather than resumed. A resumed run's `damage_taken` counts
only HP drops seen after the restart, which would put a quietly wrong row in
runs.jsonl, and damage per floor is one of the three numbers a set is read on.
The cost is losing that run; every row that does land is complete.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent
LOGS = BOT / "logs"
CONTROL = BOT / "scripts" / "bot_control.ps1"
API = "http://localhost:15526/api/v1/singleplayer"
HERE = Path(__file__).parent
WATCH_LOG = BOT / "logs" / "run_set.log"
# Set by --report-script. There is no default: the generator that existed
# for set 6 was written for set 6, and a stale report is worse than none.
REPORT_SCRIPT = None

# The game writes its own log, and it says plainly whether the mod loaded. On
# 2026-11-18 a game update broke the mod: it failed to load, the API never
# opened, and from outside that is indistinguishable from a game that is slow
# to start. Without this check the supervisor answers it with relaunches --
# ten of them, then "BROKEN", with nothing in its log naming the real cause.
GAME_LOG = Path.home() / "AppData" / "Roaming" / "SlayTheSpire2" / "logs" / "godot.log"

POLL_SECONDS = 60
STUCK_MINUTES = 8         # the bot's own freeze recovery gets ~4 min of that first
# The bot kills and relaunches the game itself, and Steam takes ~2 minutes over
# it, so the API being unreachable while the bot is alive is usually the bot
# working rather than a crash. Restarting the process there is actively harmful:
# it resets the bot's own `_consecutive_recoveries`, so the escalation that
# abandons a run after the second freeze never fires. That is exactly what
# turned one unplayable screen into 52 relaunches and 3h35m of nothing on
# 11-13 -- the counter read 1 all sixty times. Wait for several polls, and only
# act if the decision log has stopped growing too.
API_DOWN_POLLS = 4
MAX_RESTARTS = 10         # consecutive failures before giving up for the night
SETTLE_SECONDS = 150


def say(message: str) -> None:
    line = "%s  %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), message)
    print(line, flush=True)
    try:
        with open(WATCH_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _control(action: str) -> str:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(CONTROL), "-Action", action]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        return (out.stdout or "") + (out.stderr or "")
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def bot_running() -> bool:
    return "Bot: RUNNING" in _control("status")


def api_up() -> bool:
    try:
        with urllib.request.urlopen(API, timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def mod_load_failure() -> "str | None":
    """Why the mod did not load, or None if it did (or nothing is known yet).

    Returns a reason only when the game's own log is unambiguous, so a missing
    or stale log never stops a set: silence here means "no evidence", not
    "broken". Reads the tail, because the log grows to tens of MB in a session.
    """
    try:
        text = GAME_LOG.read_text(encoding="utf-8", errors="replace")[-200000:]
    except OSError:
        return None
    marker = text.rfind("RUNNING MODDED")
    if marker == -1:
        return None
    line = text[marker:text.find("\n", marker) if text.find("\n", marker) > 0 else None]
    if "Loaded 0 mods" not in line:
        return None
    why = ""
    exc = text.rfind("Exception thrown while loading mod")
    if exc != -1:
        why = " -- " + " ".join(text[exc:exc + 220].split())
    return "the game loaded 0 mods, so the API will never open%s" % why


def newest_log():
    logs = [p for p in LOGS.glob("run_*.jsonl") if p.stat().st_size > 0]
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def runs_recorded() -> int:
    path = LOGS / "runs.jsonl"
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8-sig").splitlines()
                   if line.strip())
    except OSError:
        return 0


def last_floor() -> str:
    path = LOGS / "runs.jsonl"
    try:
        lines = [l for l in path.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        row = json.loads(lines[-1])
        killer = ", ".join(e.get("name", "?") for e in (row.get("killed_by") or {}).get("enemies", []))
        return "floor %s act %s, killed by %s" % (row.get("floor_reached"),
                                                  row.get("act_reached"), killer or "?")
    except Exception:  # noqa: BLE001
        return "?"


def stop_bot() -> None:
    _control("stop")


def close_game() -> None:
    subprocess.run(["taskkill", "/IM", "SlayTheSpire2.exe", "/F"], capture_output=True)


def start_bot() -> bool:
    try:
        with open(WATCH_LOG, "a", encoding="utf-8") as fh:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(CONTROL), "-Action", "start"],
                stdout=fh, stderr=fh, cwd=str(BOT),
            )
    except OSError as exc:
        say("could not launch the control script: %s" % exc)
        return False
    for _ in range(45):                      # up to ~3.75 min for Steam + the game
        time.sleep(5)
        if bot_running() and api_up():
            return True
    return bot_running()


def recover(reason: str, attempt: int) -> bool:
    say("RECOVERING (%d): %s" % (attempt, reason))
    stop_bot()
    close_game()
    time.sleep(5)
    ok = start_bot()
    say("restart %s" % ("succeeded" if ok else "FAILED"))
    return ok


def write_report(reason: str) -> None:
    if REPORT_SCRIPT is None or not REPORT_SCRIPT.exists():
        say("no --report-script given; read the figures with "
            "scripts/damage_per_round.py")
        return
    say("writing the morning report")
    try:
        out = subprocess.run([sys.executable, str(REPORT_SCRIPT), "--reason", reason],
                             capture_output=True, text=True, timeout=600, cwd=str(BOT))
        say("report: %s" % (out.stdout.strip().splitlines() or ["(no output)"])[-1])
        if out.returncode != 0:
            say("report script failed: %s" % (out.stderr or "")[-400:])
    except Exception as exc:  # noqa: BLE001
        say("report script error: %s" % exc)


def shut_down(reason: str) -> None:
    say("%s -- stopping the bot and closing the game" % reason)
    stop_bot()
    close_game()
    say("stopped; the computer is free")
    write_report(reason)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="07:00")
    ap.add_argument("--set-size", type=int, default=20)
    ap.add_argument("--break-minutes", type=int, default=10)
    ap.add_argument("--stop-at", type=int, default=0,
                    help="stop once this many runs are recorded (0 = no limit)")
    ap.add_argument("--report-script", type=Path, default=None,
                    help="run this with --reason after the set ends")
    args = ap.parse_args()

    global REPORT_SCRIPT
    REPORT_SCRIPT = args.report_script

    hh, mm = (int(x) for x in args.until.split(":"))
    now = datetime.datetime.now()
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if deadline <= now:
        deadline += datetime.timedelta(days=1)

    count = runs_recorded()
    boundary = ((count // args.set_size) + 1) * args.set_size
    say("=" * 64)
    say("night watch: until %s, sets of %d, %d-minute breaks"
        % (deadline.strftime("%H:%M"), args.set_size, args.break_minutes))
    if args.stop_at:
        say("stopping at %d recorded runs, or %s, whichever comes first"
            % (args.stop_at, deadline.strftime("%H:%M")))
    say("starting from %d recorded runs; next break at %d" % (count, boundary))

    dead_mod = mod_load_failure()
    if dead_mod:
        say("REFUSING TO START: %s" % dead_mod)
        return 1

    restarts = 0
    api_down = 0
    last_count = count
    log = newest_log()
    last_size = log.stat().st_size if log else 0
    last_growth = time.time()

    while True:
        time.sleep(POLL_SECONDS)
        now = datetime.datetime.now()

        if now >= deadline:
            shut_down("TIME_UP at %s" % now.strftime("%H:%M"))
            return 0

        count = runs_recorded()
        if count > last_count:
            say("run %d done -- %s" % (count, last_floor()))
            last_count = count
            last_growth = time.time()

        if args.stop_at and count >= args.stop_at:
            shut_down("SET COMPLETE -- %d runs recorded" % count)
            return 0

        # --- set boundary: stop, cool, start again -------------------------
        if count >= boundary:
            say("set boundary at %d runs -- %d-minute break" % (count, args.break_minutes))
            stop_bot()
            close_game()
            resume_at = now + datetime.timedelta(minutes=args.break_minutes)
            while datetime.datetime.now() < resume_at:
                if datetime.datetime.now() >= deadline:
                    shut_down("TIME_UP during the break")
                    return 0
                time.sleep(20)
            boundary += args.set_size
            if start_bot():
                say("back up; next break at %d runs" % boundary)
                restarts = 0
            else:
                say("could not restart after the break")
                restarts += 1
            log = newest_log()
            last_size = log.stat().st_size if log else 0
            last_growth = time.time()
            api_down = 0
            continue

        # --- health --------------------------------------------------------
        alive, api = bot_running(), api_up()
        api_down = 0 if api else api_down + 1
        log = newest_log()
        size = log.stat().st_size if log else 0
        if size > last_size:
            last_size, last_growth = size, time.time()
        static_for = (time.time() - last_growth) / 60.0

        # A dead mod cannot be restarted into life, so say so and stop rather
        # than spending MAX_RESTARTS discovering it the slow way.
        if not api:
            dead_mod = mod_load_failure()
            if dead_mod:
                say("MOD NOT LOADED: %s" % dead_mod)
                shut_down("MOD NOT LOADED -- rebuild it against this game version")
                return 1

        trouble = None
        if not alive:
            trouble = "the bot process is gone"
        elif api_down >= API_DOWN_POLLS and static_for > 2.0:
            trouble = ("the game API has not answered for %d polls and the log "
                       "has been static for %.0f min" % (api_down, static_for))
        elif static_for > STUCK_MINUTES:
            trouble = "no log growth for %.0f minutes" % static_for

        if trouble:
            if restarts >= MAX_RESTARTS:
                say("BROKEN after %d restarts: %s" % (restarts, trouble))
                shut_down("BROKEN")
                return 1
            restarts += 1
            if recover(trouble, restarts):
                time.sleep(SETTLE_SECONDS)
                restarts = 0
            api_down = 0
            log = newest_log()
            last_size = log.stat().st_size if log else 0
            last_growth = time.time()
            continue

        if int(time.time()) % 900 < POLL_SECONDS:      # a heartbeat every ~15 min
            say("ok -- %d runs, bot alive, API up, log %.1f MB"
                % (count, size / 1048576))

    return 0


if __name__ == "__main__":
    sys.exit(main())
