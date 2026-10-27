"""Synthetic character echo and a prompt/read race, with no device access."""


class CharacterPort:
    timeout = 0.05
    write_timeout = 0.25

    def __init__(self, reply, *, clock=None, failure=None, fail_first=None):
        self.reply, self.clock = reply, clock
        self.failure, self.fail_first = failure, fail_first
        self.writes, self.commands = [], []
        self.incoming, self.line = bytearray(), bytearray()
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.incoming)

    def write(self, data):
        self.writes.append(bytes(data))
        first = not self.line
        failure = self.failure if self.fail_first in (None, data[:1]) else None
        if first and failure == "short_first":
            return 0
        accepted = data[2:] if first and len(data) > 1 else data
        if not first and failure == "short_remainder":
            accepted = data[:-1]
        self.line.extend(accepted)
        if not first or failure not in ("missing_echo", "wrong_echo"):
            self.incoming.extend(accepted)
        elif failure == "wrong_echo":
            self.incoming.extend(b"?")
        while b"\n" in self.line:
            line, _, rest = self.line.partition(b"\n")
            self.line = bytearray(rest)
            self.commands.append(bytes(line))
            self.incoming.extend(self.reply(bytes(line) + b"\n"))
        return len(accepted) if not first and failure == "short_remainder" else len(data)

    def read(self, size):
        if self.clock is not None:
            self.clock.sleep(self.timeout)
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data

    def reset_input_buffer(self):
        self.incoming.clear()

    def close(self):
        self.closed = True
