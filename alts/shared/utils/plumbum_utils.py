import time

from plumbum import ProcessTimedOut
from plumbum.commands.modifiers import Future


def wait_bg_process(future: Future, timeout: int):
    # For some reason, plumbum.commands.modifiers.Future.wait method
    # doesn't take timeout into account, so here is a workaround for it
    start_time = time.time()
    while not future.poll():
        if time.time() - start_time > timeout:
            future.proc.terminate()
            raise ProcessTimedOut(
                f"Process did not terminate within {timeout} seconds",
                future.proc.argv,
            )
        time.sleep(0.1)
