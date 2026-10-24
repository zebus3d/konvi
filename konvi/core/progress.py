"""Lee la salida de `ffmpeg -progress pipe:1` (bloques clave=valor). Sin Qt."""


class Progress:
    def __init__(self, duration):
        self.duration = max(float(duration or 0), 0.0)
        self.seconds = 0.0          # posición ya convertida
        self.speed = 0.0            # 2.0 = el doble de rápido que tiempo real
        self.finished = False
        self._buffer = ""

    def feed(self, text):
        """Añade texto (puede llegar cortado por cualquier sitio). Devuelve True si cambió algo."""
        self._buffer += text
        *lines, self._buffer = self._buffer.split("\n")
        changed = False
        for line in lines:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.lstrip("-").isdigit():
                self.seconds = max(0.0, int(value) / 1_000_000)
                changed = True
            elif key == "speed" and value.endswith("x"):
                try:
                    self.speed = float(value[:-1])
                except ValueError:
                    pass
            elif key == "progress" and value == "end":
                self.finished = True
                changed = True
        return changed

    @property
    def fraction(self):
        if self.finished:
            return 1.0
        if not self.duration:
            return 0.0
        return min(self.seconds / self.duration, 0.999)

    @property
    def eta(self):
        """Segundos que faltan, o None si aún no se puede estimar."""
        if not self.duration or self.speed <= 0 or self.seconds <= 0:
            return None
        return max(0.0, (self.duration - self.seconds) / self.speed)


def format_eta(seconds):
    if seconds is None:
        return ""
    s = int(seconds + 0.5)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s // 60 % 60:02d} min"
