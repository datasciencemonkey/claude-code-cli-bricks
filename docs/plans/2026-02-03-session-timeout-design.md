# Session Timeout Design

## Problem

When frontend tabs close unexpectedly (browser crash, network drop, force quit), the backend PTY sessions remain open indefinitely. The `beforeunload` beacon cleanup only works for graceful tab closes.

## Solution

Use the existing 100ms polling as an implicit heartbeat. If `/api/output` hasn't been called for 60 seconds, assume the frontend is gone and terminate the session gracefully.

## Design

### Configuration Constants

```python
SESSION_TIMEOUT_SECONDS = 60        # No poll for 60s = dead session
CLEANUP_INTERVAL_SECONDS = 30       # How often to check for stale sessions
GRACEFUL_SHUTDOWN_WAIT = 3          # Seconds to wait after SIGHUP before SIGKILL
```

### Data Model Changes

Add `last_poll_time` to session structure:

```python
sessions[session_id] = {
    "master_fd": master_fd,
    "pid": pid,
    "output_buffer": deque(maxlen=1000),
    "last_poll_time": time.time(),  # NEW
    "created_at": time.time()        # NEW
}
```

Update timestamp on every poll in `/api/output`:

```python
sessions[session_id]["last_poll_time"] = time.time()
```

### Cleanup Thread

Background thread runs every 30 seconds:

```python
def cleanup_stale_sessions():
    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)

        now = time.time()
        stale_sessions = []

        with sessions_lock:
            for session_id, session in sessions.items():
                if now - session["last_poll_time"] > SESSION_TIMEOUT_SECONDS:
                    stale_sessions.append((session_id, session["pid"], session["master_fd"]))

        for session_id, pid, master_fd in stale_sessions:
            terminate_session(session_id, pid, master_fd)
```

### Graceful Termination

SIGHUP first, wait 3 seconds, then SIGKILL if still alive:

```python
def terminate_session(session_id, pid, master_fd):
    try:
        os.kill(pid, signal.SIGHUP)
        time.sleep(GRACEFUL_SHUTDOWN_WAIT)

        try:
            os.kill(pid, 0)  # Check if still alive
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass  # Already dead

        os.close(master_fd)
    except OSError:
        pass

    with sessions_lock:
        sessions.pop(session_id, None)
```

### Thread Startup

Add before `app.run()`:

```python
cleanup_thread = threading.Thread(target=cleanup_stale_sessions, daemon=True)
cleanup_thread.start()
```

## Behavior

| Scenario | Result |
|----------|--------|
| Browser open, user idle | Polling continues, session stays alive |
| Browser closed gracefully | Beacon fires, immediate cleanup |
| Browser crash / force quit | Polling stops, cleanup after 60s |
| Network disconnect | Polling stops, cleanup after 60s |
| Tab force-closed | Polling stops, cleanup after 60s |

## Files to Modify

- `app.py` - All backend changes (data model, cleanup thread, termination logic)

## Not In Scope

- Input-based idle timeout (killing sessions where user hasn't typed)
- Maximum session limits
- Session persistence/reconnection
