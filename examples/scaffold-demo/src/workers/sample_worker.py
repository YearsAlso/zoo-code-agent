from zoo_framework.workers import BaseWorker


class SampleWorker(BaseWorker):
    """sample Worker."""

    def __init__(self):
        BaseWorker.__init__(self, {
            "is_loop": True,
            "delay_time": 10,
            "name": "sample_worker",
        })
        self._tick = 0

    def _execute(self):
        """Run the business logic."""
        self._tick += 1
        print(f"[sample_worker] tick #{self._tick}")

    def _destroy(self, result):
        """Called when the Worker is unregistered (the shutdown path triggers this)."""

    def _on_error(self):
        """Called when an execution raises."""

    def _on_done(self):
        """Called at the end of each execution (success or failure)."""
