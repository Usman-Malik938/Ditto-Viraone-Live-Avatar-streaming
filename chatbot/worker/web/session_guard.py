import threading
import time
from collections import deque

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


class CallRefusal:
    """Why a request or a new call was refused: code (rate_limited, visitor_limit or ip_limit) for the page, and how
    long until it may try again."""

    def __init__(self, code, retry_after_seconds):
        self.code = code
        self.retry_after_seconds = max(1, int(retry_after_seconds + 0.999))

    def as_dictionary(self, message):
        return {"error": message, "code": self.code, "retry_after_seconds": self.retry_after_seconds}


class SlidingWindowCounter:
    """Counts events per key over the last window_seconds; a limit of 0 means no limit."""

    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.window_seconds = window_seconds
        self.events = {}

    def wait_seconds(self, key, now):
        """0 when one more event fits now, otherwise the seconds until the oldest counted event leaves the window."""
        if self.limit <= 0:
            return 0
        recent = self.recent_events(key, now)
        if len(recent) < self.limit:
            return 0
        return recent[0] + self.window_seconds - now

    def add(self, key, now):
        if self.limit > 0:
            self.recent_events(key, now).append(now)

    def recent_events(self, key, now):
        recent = self.events.setdefault(key, deque())
        while recent and recent[0] <= now - self.window_seconds:
            recent.popleft()
        return recent

    def forget_idle_keys(self, now):
        for key in list(self.events):
            if not self.recent_events(key, now):
                del self.events[key]


class SessionGuard:
    """The session safeguards on the web server's side (PoC agreement, section 3), shared with the voice agent in the
    same process:
    - every request: at most requests_per_minute per IP address;
    - new widget calls: at most calls_per_visitor_per_hour per visitor token, counting calls the avatar joined (so a
      call that never connects isn't held against the visitor), and calls_per_ip_per_hour per IP address, counting
      every call ticket handed out (the backstop against scripts and cleared visitor tokens);
    - one active call per visitor token: a new call ends the visitor's previous one.
    The call's own time and silence limits are enforced by the agent (worker/livekit/call_limits.py)."""

    def __init__(self, requests_per_minute, calls_per_visitor_per_hour, calls_per_ip_per_hour, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.requests_by_ip = SlidingWindowCounter(requests_per_minute, SECONDS_PER_MINUTE)
        self.calls_by_visitor = SlidingWindowCounter(calls_per_visitor_per_hour, SECONDS_PER_HOUR)
        self.calls_by_ip = SlidingWindowCounter(calls_per_ip_per_hour, SECONDS_PER_HOUR)
        self.room_by_visitor = {}
        self.visitor_by_room = {}
        self.end_call_by_room = {}
        self.last_cleanup_at = clock()

    def check_request(self, ip_address):
        """Counts the request; returns a CallRefusal when the IP address is over its per-minute limit, else None."""
        with self.lock:
            now = self.clock()
            self.forget_idle_entries(now)
            wait_seconds = self.requests_by_ip.wait_seconds(ip_address, now)
            if wait_seconds > 0:
                return CallRefusal("rate_limited", wait_seconds)
            self.requests_by_ip.add(ip_address, now)
            return None

    def start_widget_call(self, visitor_token, ip_address, room_name):
        """Checks the hourly limits for a new widget call; returns a CallRefusal when over one, else None. The visitor's
        previous call, if still running, is ended."""
        with self.lock:
            now = self.clock()
            wait_seconds = self.calls_by_visitor.wait_seconds(visitor_token, now)
            if wait_seconds > 0:
                return CallRefusal("visitor_limit", wait_seconds)
            wait_seconds = self.calls_by_ip.wait_seconds(ip_address, now)
            if wait_seconds > 0:
                return CallRefusal("ip_limit", wait_seconds)
            self.calls_by_ip.add(ip_address, now)
            previous_room = self.room_by_visitor.get(visitor_token)
            self.room_by_visitor[visitor_token] = room_name
            self.visitor_by_room[room_name] = visitor_token
            end_previous_call = self.end_call_by_room.pop(previous_room, None)
        if end_previous_call is not None:
            end_previous_call("replaced")
        return None

    def call_started(self, room_name, end_call):
        """The agent registers its call; end_call(reason) must be safe to call from another thread. A widget call counts
        toward its visitor's hourly limit from here. A call whose visitor has already started a newer one is ended
        at once and not counted."""
        with self.lock:
            visitor_token = self.visitor_by_room.get(room_name)
            superseded = visitor_token is not None and self.room_by_visitor.get(visitor_token) != room_name
            if not superseded:
                self.end_call_by_room[room_name] = end_call
                if visitor_token is not None:
                    self.calls_by_visitor.add(visitor_token, self.clock())
        if superseded:
            end_call("replaced")

    def call_ended(self, room_name):
        with self.lock:
            self.end_call_by_room.pop(room_name, None)
            visitor_token = self.visitor_by_room.pop(room_name, None)
            if visitor_token is not None and self.room_by_visitor.get(visitor_token) == room_name:
                del self.room_by_visitor[visitor_token]

    def forget_idle_entries(self, now):
        """Once a minute, drops counters with nothing left in their window, so memory doesn't grow with visitors."""
        if now - self.last_cleanup_at < SECONDS_PER_MINUTE:
            return
        self.last_cleanup_at = now
        self.requests_by_ip.forget_idle_keys(now)
        self.calls_by_visitor.forget_idle_keys(now)
        self.calls_by_ip.forget_idle_keys(now)
