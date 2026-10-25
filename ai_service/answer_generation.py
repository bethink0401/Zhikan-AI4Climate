"""Request-scoped cancellation and progress for model answer rewrites."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event
import time


class AnswerGenerationCancelled(Exception):
    """The client has disconnected; don't save a partial answer."""


@dataclass
class GenerationControl:
    cancelled: Event
    on_retry: Callable[[int], None]


_control: ContextVar[GenerationControl | None] = ContextVar("answer_generation", default=None)


@contextmanager
def answer_generation(cancelled: Event, on_retry: Callable[[int], None]) -> Iterator[None]:
    token = _control.set(GenerationControl(cancelled, on_retry))
    try:
        yield
    finally:
        _control.reset(token)


def check_generation_active() -> None:
    control = _control.get()
    if control and control.cancelled.is_set():
        raise AnswerGenerationCancelled()


def wait_for_answer_retry(attempt: int) -> None:
    check_generation_active()
    control = _control.get()
    delay = min(attempt * 0.2, 1.0)
    if control:
        control.on_retry(attempt)
        if control.cancelled.wait(delay):
            raise AnswerGenerationCancelled()
    else:
        time.sleep(delay)
